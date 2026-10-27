from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from . import services
from .models import HistorialEstado, Incidencia
from .test_ordenes import BaseOrdenes


def post_recepcion(lineas, resultados):
    """resultados: lista paralela a `lineas` con ('ok',) o ('ng', 'descripción'[, 'cantidad'])."""
    datos = {}
    for linea, r in zip(lineas, resultados):
        datos[f"res_{linea.pk}"] = r[0]
        if r[0] == "ng":
            datos[f"cat_{linea.pk}"] = "otra"
            datos[f"desc_{linea.pk}"] = r[1] if len(r) > 1 else ""
            datos[f"cant_{linea.pk}"] = r[2] if len(r) > 2 else ""
    return datos


class PermisosRecepcionTests(BaseOrdenes):
    def test_anonimo_va_al_login(self):
        o = self.crear_orden("en_camino")
        for url in (reverse("recepcion_lista"), reverse("recepcion_verificar", args=[o.pk])):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertIn("/login/", r["Location"])

    def test_solo_almacen_y_admin_entran(self):
        o = self.crear_orden("en_camino")
        for usuario, esperado in [("almacen", 200), ("admin", 200), ("compras", 403), ("produccion", 403)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse("recepcion_lista")).status_code, esperado, usuario)
            self.assertEqual(self.client.get(reverse("recepcion_verificar", args=[o.pk])).status_code, esperado, usuario)

    def test_compras_no_puede_registrar_por_post(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("compras")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]), post_recepcion(lineas, [("ok",), ("ok",)]))
        self.assertEqual(r.status_code, 403)
        o.refresh_from_db()
        self.assertEqual(o.estado, "en_camino")

    def test_menu_recepcion_enlaza_para_almacen(self):
        self.entrar("almacen")
        r = self.client.get(reverse("recepcion_lista"))
        self.assertContains(r, reverse("recepcion_lista"))


class ListadoRecepcionTests(BaseOrdenes):
    def test_solo_muestra_ordenes_pendientes_de_recibir(self):
        pendiente = self.crear_orden("en_camino")
        for estado in ("creada", "incidencia", "incidencia_resuelta", "recibido"):
            self.crear_orden(estado)
        self.entrar("almacen")
        r = self.client.get(reverse("recepcion_lista"))
        codigos = {o.pk for o in r.context["ordenes"]}
        self.assertEqual(codigos, {pendiente.pk})

    def test_mas_antiguas_primero(self):
        a = self.crear_orden("en_camino")
        b = self.crear_orden("en_camino")
        self.entrar("almacen")
        r = self.client.get(reverse("recepcion_lista"))
        self.assertEqual([o.pk for o in r.context["ordenes"]], [a.pk, b.pk])

    def test_vacio(self):
        self.entrar("almacen")
        self.assertContains(self.client.get(reverse("recepcion_lista")), "No hay órdenes pendientes")


class RecepcionTodoOkTests(BaseOrdenes):
    def test_todo_ok_deja_la_orden_recibida(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]), post_recepcion(lineas, [("ok",), ("ok",)]))
        self.assertRedirects(r, reverse("recepcion_lista"))
        o.refresh_from_db()
        self.assertEqual(o.estado, "recibido")
        for l in o.lineas.all():
            self.assertEqual(l.resultado_verificacion, "ok")
            self.assertEqual(l.cantidad_verificada, l.cantidad_pedida)
        self.assertEqual(Incidencia.objects.count(), 0)

    def test_deja_traza_en_el_historial(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        self.client.post(reverse("recepcion_verificar", args=[o.pk]), post_recepcion(lineas, [("ok",), ("ok",)]))
        h = HistorialEstado.objects.filter(orden=o).latest("pk")
        self.assertEqual((h.estado_anterior, h.estado_nuevo, h.usuario), ("en_camino", "recibido", self.usuarios["almacen"]))

    def test_mensaje_de_exito(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]),
                             post_recepcion(lineas, [("ok",), ("ok",)]), follow=True)
        self.assertContains(r, "recibida correctamente")

    def test_el_admin_tambien_puede_verificar(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("admin")
        self.client.post(reverse("recepcion_verificar", args=[o.pk]), post_recepcion(lineas, [("ok",), ("ok",)]))
        o.refresh_from_db()
        self.assertEqual(o.estado, "recibido")


class RecepcionConIncidenciaTests(BaseOrdenes):
    def test_una_linea_ng_crea_incidencia_y_cambia_el_estado(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]),
                             post_recepcion(lineas, [("ok",), ("ng", "Sacos rotos", "1.5")]), follow=True)
        o.refresh_from_db()
        self.assertEqual(o.estado, "incidencia")
        self.assertEqual(Incidencia.objects.count(), 1)
        i = Incidencia.objects.get()
        self.assertEqual((i.linea, i.descripcion, i.creado_por), (lineas[1], "Otra: Sacos rotos"[0:0] + i.descripcion, self.usuarios["almacen"]))
        self.assertIn("Sacos rotos", i.descripcion)
        self.assertFalse(i.resuelta)
        lineas[1].refresh_from_db()
        self.assertEqual(lineas[1].resultado_verificacion, "ng")
        self.assertContains(r, "1 incidencia.")

    def test_la_incidencia_nace_como_ticket_abierto_con_su_mensaje(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        self.client.post(reverse("recepcion_verificar", args=[o.pk]), post_recepcion(lineas, [("ok",), ("ng", "Sacos rotos")]))
        inc = Incidencia.objects.get()
        self.assertEqual(inc.fase, "abierta")
        m = inc.mensajes.get()
        self.assertEqual((m.tipo, m.autor, m.rol), ("apertura", self.usuarios["almacen"], "Almacén"))
        self.assertIn("Sacos rotos", m.texto)

    def test_dos_lineas_ng_crean_dos_incidencias(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]),
                             post_recepcion(lineas, [("ng", "Faltan bultos"), ("ng", "Caducado")]), follow=True)
        self.assertEqual(Incidencia.objects.count(), 2)
        self.assertContains(r, "2 incidencias.")

    def test_ng_sin_descripcion_no_guarda_nada(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]),
                             post_recepcion(lineas, [("ok",), ("ng", "   ")]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Describe el problema")
        o.refresh_from_db()
        self.assertEqual(o.estado, "en_camino")
        self.assertEqual(Incidencia.objects.count(), 0)
        self.assertFalse(o.lineas.exclude(resultado_verificacion=None).exists())

    def test_linea_sin_marcar_no_guarda_nada(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]), {f"res_{lineas[0].pk}": "ok"})
        self.assertContains(r, "Marca OK o NG")
        o.refresh_from_db()
        self.assertEqual(o.estado, "en_camino")

    def test_el_formulario_conserva_lo_escrito_al_fallar(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]),
                             post_recepcion(lineas, [("ok",), ("ng", "")]))
        self.assertContains(r, f'name="res_{lineas[0].pk}" value="ok" checked')
        self.assertContains(r, f'name="res_{lineas[1].pk}" value="ng" checked')

    def test_cantidad_negativa_es_invalida(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]),
                             post_recepcion(lineas, [("ok",), ("ng", "Falta", "-3")]))
        self.assertEqual(r.status_code, 200)
        o.refresh_from_db()
        self.assertEqual(o.estado, "en_camino")


class EstadosYServicioTests(BaseOrdenes):
    def test_no_se_puede_verificar_una_orden_creada_ni_recibida(self):
        for estado in ("creada", "incidencia", "incidencia_resuelta", "recibido"):
            o = self.crear_orden(estado)
            self.entrar("almacen")
            r = self.client.get(reverse("recepcion_verificar", args=[o.pk]))
            self.assertRedirects(r, reverse("recepcion_lista"), msg_prefix=estado)
            lineas = list(o.lineas.order_by("pk"))
            self.client.post(reverse("recepcion_verificar", args=[o.pk]), post_recepcion(lineas, [("ok",), ("ok",)]))
            o.refresh_from_db()
            self.assertEqual(o.estado, estado)

    def test_servicio_rechaza_lineas_que_no_corresponden(self):
        o = self.crear_orden("en_camino")
        l1, _ = list(o.lineas.order_by("pk"))
        with self.assertRaises(services.TransicionInvalida):
            services.registrar_recepcion(o, self.usuarios["almacen"], {l1.pk: {"resultado": "ok"}})

    def test_servicio_rechaza_ng_sin_descripcion_y_deshace_todo(self):
        o = self.crear_orden("en_camino")
        l1, l2 = list(o.lineas.order_by("pk"))
        with self.assertRaises(ValueError):
            services.registrar_recepcion(o, self.usuarios["almacen"], {
                l1.pk: {"resultado": "ok"}, l2.pk: {"resultado": "ng", "descripcion": " "}})
        l1.refresh_from_db(); o.refresh_from_db()
        self.assertIsNone(l1.resultado_verificacion)       # el OK de la primera línea se deshizo
        self.assertEqual(o.estado, "en_camino")


class PantallasTests(BaseOrdenes):
    def test_detalle_muestra_boton_a_almacen_solo_si_toca(self):
        o = self.crear_orden("en_camino")
        url = reverse("recepcion_verificar", args=[o.pk])
        self.entrar("almacen")
        self.assertContains(self.client.get(reverse("orden_detalle", args=[o.pk])), url)
        self.entrar("compras")
        self.assertNotContains(self.client.get(reverse("orden_detalle", args=[o.pk])), url)
        o2 = self.crear_orden("creada")
        self.entrar("almacen")
        self.assertEqual(self.client.get(reverse("orden_detalle", args=[o2.pk])).status_code, 404)

    def test_detalle_muestra_las_incidencias(self):
        o = self.crear_orden("en_camino")
        lineas = list(o.lineas.order_by("pk"))
        self.entrar("almacen")
        self.client.post(reverse("recepcion_verificar", args=[o.pk]),
                         post_recepcion(lineas, [("ok",), ("ng", "Sacos rotos")]))
        r = self.client.get(reverse("orden_detalle", args=[o.pk]))
        self.assertContains(r, "Sacos rotos")
        self.assertContains(r, "Abierta")

    def test_pantalla_de_verificacion_renderiza_cada_linea(self):
        o = self.crear_orden("en_camino")
        self.entrar("almacen")
        r = self.client.get(reverse("recepcion_verificar", args=[o.pk]))
        self.assertContains(r, "Etanol 96%")
        self.assertContains(r, "Sosa cáustica")
        self.assertContains(r, "Marcar todo OK")
