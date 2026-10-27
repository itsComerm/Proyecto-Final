from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import OrdenCompra
from .test_incidencias import BaseIncidencias
from .test_recepcion import post_recepcion


class AutorizacionesTests(BaseIncidencias):
    def url(self):
        return reverse("autorizacion_lista")

    def test_anonimo_va_al_login(self):
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    def test_solo_administrador_entra(self):
        for usuario, esperado in [("admin", 200), ("compras", 403), ("almacen", 403), ("produccion", 403)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(self.url()).status_code, esperado, usuario)

    def test_lista_solo_las_creadas_de_la_mas_antigua_a_la_mas_reciente(self):
        a = self.crear_orden("creada"); b = self.crear_orden("creada")
        self.crear_orden("en_camino"); self.crear_orden("recibido")
        self.entrar("admin")
        r = self.client.get(self.url())
        self.assertEqual([o.pk for o in r.context["ordenes"]], [a.pk, b.pk])
        self.assertContains(r, a.codigo)
        self.assertContains(r, "Autorizar y enviar")

    def test_tarjeta_muestra_lineas_proveedor_y_email_de_destino(self):
        o = self.crear_orden("creada")
        self.entrar("admin")
        r = self.client.get(self.url())
        for linea in o.lineas.all():
            self.assertContains(r, linea.producto.nombre)
        self.assertContains(r, o.proveedor.email)
        self.assertContains(r, reverse("orden_detalle", args=[o.pk]))
        self.assertContains(r, 'target="_blank"')

    def test_estado_vacio(self):
        self.entrar("admin")
        r = self.client.get(self.url())
        self.assertContains(r, "No hay órdenes pendientes de autorizar")
        self.assertNotContains(r, "Expandir todas")

    def test_autorizar_desde_la_lista_vuelve_a_la_lista_y_envia_el_email(self):
        o = self.crear_orden("creada")
        self.entrar("admin")
        r = self.client.post(reverse("orden_autorizar", args=[o.pk]), {"next": "autorizaciones"})
        self.assertRedirects(r, self.url())
        o.refresh_from_db()
        self.assertEqual(o.estado, "en_camino")
        self.assertEqual(len(mail.outbox), 1)

    def test_autorizar_sin_next_vuelve_al_detalle_y_next_desconocido_se_ignora(self):
        o = self.crear_orden("creada"); o2 = self.crear_orden("creada")
        self.entrar("admin")
        self.assertRedirects(self.client.post(reverse("orden_autorizar", args=[o.pk])),
                             reverse("orden_detalle", args=[o.pk]))
        r = self.client.post(reverse("orden_autorizar", args=[o2.pk]), {"next": "https://malo.example/"})
        self.assertRedirects(r, reverse("orden_detalle", args=[o2.pk]))

    # ---- contador del menú ----
    def test_contador_en_el_menu_del_administrador(self):
        self.entrar("admin")
        r = self.client.get(reverse("orden_lista"))
        menu = {i["etiqueta"]: i["contador"] for g in r.context["menu"] for i in g["items"]}
        self.assertEqual(menu["Autorizaciones"], 0)
        self.crear_orden("creada"); self.crear_orden("creada")
        r = self.client.get(reverse("orden_lista"))
        menu = {i["etiqueta"]: i["contador"] for g in r.context["menu"] for i in g["items"]}
        self.assertEqual(menu["Autorizaciones"], 2)

    def test_otros_roles_no_ven_autorizaciones_en_el_menu(self):
        self.crear_orden("creada")
        for usuario in ("compras", "almacen", "produccion"):
            self.entrar(usuario)
            r = self.client.get(reverse("incidencia_lista") if usuario != "produccion" else reverse("orden_lista"))
            etiquetas = [i["etiqueta"] for g in r.context["menu"] for i in g["items"]]
            self.assertNotIn("Autorizaciones", etiquetas, usuario)

    def test_sin_n_mas_uno(self):
        def consultas():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(self.url()).status_code, 200)
            return len(ctx)
        for _ in range(2):
            self.crear_orden("creada")
        self.entrar("admin")
        pocas = consultas()
        for _ in range(6):
            self.crear_orden("creada")
        self.assertEqual(consultas(), pocas)


class DashboardAdminTests(BaseIncidencias):
    def url(self):
        return reverse("dashboard_admin")

    def cargar(self):
        self.entrar("admin")
        return self.client.get(self.url())

    def test_permisos(self):
        for usuario, esperado in [("admin", 200), ("compras", 403), ("almacen", 403), ("produccion", 403)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(self.url()).status_code, esperado, usuario)

    def test_inicio_redirige_al_dashboard(self):
        self.entrar("admin")
        self.assertRedirects(self.client.get(reverse("inicio")), self.url())

    def test_base_vacia(self):
        r = self.cargar()
        self.assertEqual(r.context["t"]["por_autorizar"], 0)
        self.assertContains(r, "No hay órdenes pendientes de autorizar")
        self.assertContains(r, "No hay incidencias abiertas")
        self.assertContains(r, "Todavía no hay actividad")

    def test_tarjetas(self):
        self.crear_orden("creada"); self.crear_orden("creada"); self.crear_orden("en_camino")
        o, (inc,) = self.orden_con_incidencias()
        self.proponer(inc)                      # ahora le toca al administrador
        r = self.cargar()
        t = r.context["t"]
        self.assertEqual(t["por_autorizar"], 2)
        self.assertEqual(t["en_camino"], 1)
        self.assertEqual(t["incidencias_admin"], 1)
        self.assertEqual(t["incidencias_abiertas"], 1)
        self.assertContains(r, "2 pendientes")                 # cinta de novedad
        self.assertContains(r, "hay-novedad")
        self.assertContains(r, "Aceptar o rechazar la solución")

    def test_actividad_reciente_con_usuario(self):
        o = self.crear_orden("creada")
        self.entrar("admin")
        self.client.post(reverse("orden_autorizar", args=[o.pk]))
        r = self.client.get(self.url())
        self.assertEqual(r.context["actividad"][0].estado_nuevo, "en_camino")
        self.assertContains(r, "ha autorizado la orden y se ha enviado al proveedor")
        self.assertEqual(len(r.context["actividad"]), 2)

    def test_consultas_constantes(self):
        def consultas():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(self.url()).status_code, 200)
            return len(ctx)
        self.crear_orden("creada")
        self.entrar("admin")
        pocas = consultas()
        for _ in range(4):
            self.crear_orden("creada")
        self.orden_con_incidencias()
        self.entrar("admin")
        self.assertEqual(consultas(), pocas)


class IncidenciasDelDashboardAdminTests(BaseIncidencias):
    def cargar(self, filtro=""):
        self.entrar("admin")
        return self.client.get(reverse("dashboard_admin") + (f"?incidencias={filtro}" if filtro else ""))

    def test_por_defecto_muestra_todas_las_abiertas_con_alerta_en_las_suyas(self):
        _, (a,) = self.orden_con_incidencias()          # abierta: le toca a Compras
        _, (b,) = self.orden_con_incidencias()
        self.proponer(b)                                  # propuesta: le toca al Administrador
        r = self.cargar()
        self.assertEqual(r.context["filtro_incidencias"], "abiertas")
        self.assertEqual({i.pk for i in r.context["incidencias"]}, {a.pk, b.pk})
        self.assertEqual(r.context["incidencias"][0].pk, b.pk)          # las suyas primero
        self.assertTrue(r.context["incidencias"][0].requiere_mi_accion)
        self.assertFalse(r.context["incidencias"][1].requiere_mi_accion)
        self.assertEqual(r.context["t"]["incidencias_abiertas"], 2)
        self.assertEqual(r.context["t"]["incidencias_admin"], 1)
        self.assertContains(r, "Requiere tu acción")
        self.assertContains(r, "Espera a Compras")
        self.assertContains(r, "1 requiere tu acción")

    def test_el_filtro_deja_solo_las_que_requieren_su_accion(self):
        self.orden_con_incidencias()
        _, (b,) = self.orden_con_incidencias()
        self.proponer(b)
        r = self.cargar("accion")
        self.assertEqual([i.pk for i in r.context["incidencias"]], [b.pk])

    def test_las_resueltas_no_aparecen(self):
        _, (a,) = self.orden_con_incidencias()
        self.avanzar(a, "solucionada")
        self.paso("admin", a, "cerrar", texto="")
        r = self.cargar()
        self.assertEqual(r.context["incidencias"], [])
        self.assertEqual(r.context["t"]["incidencias_abiertas"], 0)

    def test_las_tarjetas_sin_novedades_no_se_destacan_y_hay_solo_tres(self):
        r = self.cargar()
        self.assertNotContains(r, "hay-novedad\"")
        self.assertNotContains(r, "Órdenes creadas este mes")
        self.assertNotContains(r, "Proveedores con más incidencias")
        self.assertEqual(r.content.decode().count("of-stat dash-card"), 3)

    def test_actividad_limitada_a_cinco(self):
        for _ in range(6):
            self.crear_orden("en_camino")
        r = self.cargar()
        self.assertEqual(len(r.context["actividad"]), 5)
