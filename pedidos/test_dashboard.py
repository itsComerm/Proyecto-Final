from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import HistorialEstado, OrdenCompra
from .test_incidencias import BaseIncidencias
from .test_recepcion import post_recepcion


class DashboardAlmacenTests(BaseIncidencias):
    def url(self):
        return reverse("dashboard_almacen")

    def cargar(self, usuario="almacen"):
        self.entrar(usuario)
        return self.client.get(self.url())

    # ---- permisos ----
    def test_anonimo_va_al_login(self):
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    def test_solo_almacen_entra(self):
        for usuario, esperado in [("almacen", 200), ("compras", 403), ("produccion", 403)]:
            self.assertEqual(self.cargar(usuario).status_code, esperado, usuario)

    def test_inicio_redirige_a_este_dashboard(self):
        self.entrar("almacen")
        self.assertRedirects(self.client.get(reverse("inicio")), self.url())

    # ---- tarjetas ----
    def test_tarjetas_con_la_base_vacia(self):
        r = self.cargar()
        t = r.context["t"]
        self.assertEqual((t["en_camino"], t["incidencias_abiertas"], t["requieren_accion"]), (0, 0, 0))
        self.assertContains(r, "Nada pendiente de recibir")
        self.assertContains(r, "No hay órdenes pendientes de recibir")
        self.assertContains(r, "No hay incidencias abiertas")
        self.assertNotContains(r, "hay-novedad\"")                      # sin novedades, tarjetas blancas
        self.assertEqual(r.content.decode().count("of-stat dash-card"), 3)

    def test_en_camino_cuenta_y_se_destaca(self):
        self.crear_orden("en_camino"); self.crear_orden("en_camino"); self.crear_orden("creada")
        r = self.cargar()
        self.assertEqual(r.context["t"]["en_camino"], 2)
        self.assertIsNotNone(r.context["t"]["mas_antigua"])
        self.assertContains(r, "2 por recibir")
        self.assertContains(r, "hay-novedad")

    def test_incidencias_abiertas_y_requieren_accion(self):
        o, (inc,) = self.orden_con_incidencias()
        r = self.cargar()
        self.assertEqual(r.context["t"]["incidencias_abiertas"], 1)
        self.assertEqual(r.context["t"]["requieren_accion"], 0)
        self.assertContains(r, "Ninguna espera tu acción")
        self.avanzar(inc, "aceptada", responsable="Almacén")
        r = self.cargar()
        self.assertEqual(r.context["t"]["requieren_accion"], 1)
        self.assertContains(r, "1 requiere tu acción")
        self.assertContains(r, "1 para ti")
        self.assertContains(r, "Ejecutar la medida aceptada")

    def test_las_resueltas_no_cuentan_como_abiertas(self):
        o, (inc,) = self.orden_con_incidencias()
        self.avanzar(inc, "solucionada"); self.paso("admin", inc, "cerrar", texto="")
        r = self.cargar()
        self.assertEqual(r.context["t"]["incidencias_abiertas"], 0)
        self.assertEqual(r.context["incidencias"], [])

    # ---- tablas ----
    def test_ordenes_por_recibir_con_boton_verificar_y_mas_antigua_primero(self):
        a = self.crear_orden("en_camino"); b = self.crear_orden("en_camino"); self.crear_orden("creada")
        from datetime import timedelta
        from django.utils import timezone
        HistorialEstado.objects.filter(orden=b).update(fecha=timezone.now() - timedelta(days=3))
        r = self.cargar()
        self.assertEqual([o.pk for o in r.context["por_recibir"]], [b.pk, a.pk])
        self.assertContains(r, reverse("recepcion_verificar", args=[a.pk]))
        self.assertContains(r, "Verificar")

    def test_las_verificadas_ya_no_salen_en_por_recibir(self):
        o = self.crear_orden("en_camino")
        self.entrar("almacen")
        self.client.post(reverse("recepcion_verificar", args=[o.pk]), post_recepcion(list(o.lineas.order_by("pk")), [("ok",), ("ok",)]))
        self.assertEqual(self.cargar().context["por_recibir"], [])

    def test_incidencias_todas_por_defecto_las_suyas_primero_y_filtro(self):
        _, (a,) = self.orden_con_incidencias()                      # espera a Compras
        _, (b,) = self.orden_con_incidencias()
        self.avanzar(b, "aceptada", responsable="Almacén")          # le toca a Almacén
        r = self.cargar()
        self.assertEqual([i.pk for i in r.context["incidencias"]], [b.pk, a.pk])
        self.assertTrue(r.context["incidencias"][0].requiere_mi_accion)
        self.assertEqual(r.context["incidencias"][1].espera_a, "Compras")
        self.assertContains(r, "Requiere tu acción")
        self.assertContains(r, "Espera a Compras")
        self.entrar("almacen")
        filtradas = self.client.get(self.url(), {"incidencias": "accion"}).context["incidencias"]
        self.assertEqual([i.pk for i in filtradas], [b.pk])

    def test_las_tablas_se_limitan_a_las_ultimas_filas(self):
        for _ in range(10):
            self.orden_con_incidencias()
        for _ in range(10):
            self.crear_orden("en_camino")
        r = self.cargar()
        self.assertEqual(len(r.context["por_recibir"]), 8)
        self.assertEqual(len(r.context["incidencias"]), 8)

    def test_no_hace_una_consulta_por_fila(self):
        self.orden_con_incidencias()
        self.crear_orden("en_camino")
        self.cargar()
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(self.url())
        for _ in range(5):
            self.orden_con_incidencias(ng=(0, 1))
            self.crear_orden("en_camino")
        self.entrar("almacen")
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(self.url())
        self.assertEqual(len(pocas), len(muchas))


class DashboardComprasTests(BaseIncidencias):
    def url(self):
        return reverse("dashboard_compras")

    def cargar(self, usuario="compras", **filtros):
        self.entrar(usuario)
        return self.client.get(self.url(), filtros)

    def test_solo_compras_entra_y_el_inicio_redirige(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        for usuario, esperado in [("compras", 200), ("almacen", 403), ("produccion", 403), ("admin", 403)]:
            self.assertEqual(self.cargar(usuario).status_code, esperado, usuario)
        self.entrar("compras")
        self.assertRedirects(self.client.get(reverse("inicio")), self.url())

    def test_base_vacia_tres_tarjetas_y_boton_nueva_orden(self):
        r = self.cargar()
        t = r.context["t"]
        self.assertEqual((t["en_camino"], t["incidencias_abiertas"], t["requieren_accion"]), (0, 0, 0))
        self.assertEqual(r.content.decode().count("of-stat dash-card"), 3)
        self.assertNotContains(r, "Pendientes de autorizar")
        self.assertNotContains(r, "hay-novedad\"")
        self.assertContains(r, reverse("orden_nueva"))
        self.assertContains(r, "No hay órdenes pendientes de cerrar")
        self.assertContains(r, "No hay incidencias abiertas")

    def test_tarjetas_cuentan_en_camino_abiertas_y_las_que_le_tocan(self):
        self.crear_orden("en_camino")
        _, (inc,) = self.orden_con_incidencias()
        r = self.cargar()
        t = r.context["t"]
        self.assertEqual((t["en_camino"], t["incidencias_abiertas"], t["requieren_accion"]), (1, 1, 1))
        self.assertContains(r, "1 para ti")
        self.assertContains(r, "1 requiere tu acción")
        self.assertContains(r, "Proponer una solución")
        self.proponer(inc)                                           # ahora le toca al administrador
        r = self.cargar()
        self.assertEqual(r.context["t"]["requieren_accion"], 0)
        self.assertEqual(r.context["t"]["incidencias_abiertas"], 1)
        self.assertContains(r, "Ninguna espera tu acción")

    def test_ordenes_por_defecto_solo_las_sin_cerrar_de_cualquier_usuario(self):
        creada = self.crear_orden("creada")
        camino = self.crear_orden("en_camino")
        recibida = self.crear_orden("recibido")
        r = self.cargar()
        self.assertEqual(r.context["filtro_ordenes"], "por_cerrar")
        self.assertEqual({o.pk for o in r.context["ordenes"]}, {creada.pk, camino.pk})
        self.assertEqual(r.context["t"]["por_cerrar"], 2)
        self.assertEqual(r.context["t"]["ordenes_total"], 3)

    def test_el_filtro_todas_incluye_las_completadas(self):
        for estado in ("creada", "en_camino", "recibido", "incidencia_resuelta"):
            self.crear_orden(estado)
        r = self.cargar(ordenes="todas")
        self.assertEqual(r.context["filtro_ordenes"], "todas")
        self.assertEqual(len(r.context["ordenes"]), 4)
        self.assertContains(r, "Recibida")

    def test_una_orden_con_incidencia_cuenta_como_por_cerrar_y_al_cerrarla_deja_de_serlo(self):
        o, (inc,) = self.orden_con_incidencias()
        self.assertIn(o.pk, [x.pk for x in self.cargar().context["ordenes"]])
        self.avanzar(inc, "solucionada"); self.paso("admin", inc, "cerrar", texto="")
        self.assertNotIn(o.pk, [x.pk for x in self.cargar().context["ordenes"]])
        self.assertIn(o.pk, [x.pk for x in self.cargar(ordenes="todas").context["ordenes"]])

    def test_incidencias_todas_las_suyas_primero_y_filtro(self):
        _, (a,) = self.orden_con_incidencias()
        _, (b,) = self.orden_con_incidencias()
        self.proponer(a)                                             # a espera al admin; b a Compras
        r = self.cargar()
        self.assertEqual([i.pk for i in r.context["incidencias"]], [b.pk, a.pk])
        self.assertTrue(r.context["incidencias"][0].requiere_mi_accion)
        self.assertContains(r, "Espera a Administrador")
        self.assertEqual([i.pk for i in self.cargar(incidencias="accion").context["incidencias"]], [b.pk])

    def test_los_filtros_se_conservan_entre_si_en_los_enlaces(self):
        r = self.cargar(ordenes="todas", incidencias="accion")
        self.assertContains(r, "?ordenes=todas&incidencias=abiertas#incidencias")
        self.assertContains(r, "?ordenes=por_cerrar&incidencias=accion#ordenes")

    def test_consultas_constantes(self):
        for _ in range(2):
            self.crear_orden("en_camino")
        self.orden_con_incidencias()
        self.cargar()
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(self.url())
        for _ in range(4):
            self.crear_orden("en_camino")
            self.orden_con_incidencias(ng=(0, 1))
        self.entrar("compras")
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(self.url())
        self.assertEqual(len(pocas), len(muchas))
