from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import HistorialEstado, MensajeIncidencia
from .test_incidencias import BaseIncidencias


class ActividadTests(BaseIncidencias):
    def cargar(self, **filtros):
        self.entrar("admin")
        return self.client.get(reverse("actividad"), filtros)

    def test_permisos_y_menu(self):
        self.assertEqual(self.client.get(reverse("actividad")).status_code, 302)
        for usuario, esperado in [("admin", 200), ("compras", 403), ("almacen", 403), ("produccion", 403)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse("actividad")).status_code, esperado, usuario)
        r = self.cargar()
        menu = [i for g in r.context["menu"] for i in g["items"] if i["etiqueta"] == "Actividad"][0]
        self.assertEqual(menu["url"], reverse("actividad"))
        self.assertTrue(menu["activo"])

    def test_vacia(self):
        r = self.cargar()
        self.assertEqual(r.context["dias"], [])
        self.assertContains(r, "Sin actividad")

    def test_mezcla_estados_e_hitos_de_incidencia_y_comentarios_con_frases_naturales(self):
        o, (inc,) = self.orden_con_incidencias()
        self.paso("almacen", inc, "comentar", texto="Los palets siguen en el muelle")
        r = self.cargar()
        self.assertEqual(len(r.context["dias"]), 1)
        self.assertEqual(r.context["dias"][0]["etiqueta"], "Hoy")
        for frase in ("ha autorizado la orden y se ha enviado al proveedor", "ha verificado la recepción: hay material con incidencia",
                      "ha abierto una incidencia en", "ha comentado en la incidencia de", "Los palets siguen en el muelle"):
            self.assertContains(r, frase)
        fechas = [e["fecha"] for e in r.context["dias"][0]["eventos"]]
        self.assertEqual(fechas, sorted(fechas, reverse=True))          # lo más reciente primero
        self.assertContains(r, reverse("incidencia_detalle", args=[inc.pk]))

    def test_filtro_por_tipo(self):
        self.orden_con_incidencias()
        so = [e["tipo"] for d in self.cargar(tipo="ordenes").context["dias"] for e in d["eventos"]]
        si = [e["tipo"] for d in self.cargar(tipo="incidencias").context["dias"] for e in d["eventos"]]
        self.assertTrue(so and set(so) == {"estado"})
        self.assertTrue(si and set(si) == {"incidencia"})

    def test_filtro_por_usuario(self):
        self.orden_con_incidencias()
        almacen = self.usuarios["almacen"]
        r = self.cargar(usuario=almacen.pk)
        for d in r.context["dias"]:
            for e in d["eventos"]:
                autor = e["h"].usuario if e["tipo"] == "estado" else e["m"].autor
                self.assertEqual(autor, almacen)
        self.assertGreater(r.context["n_eventos"], 0)

    def test_busqueda_por_codigo_con_distintos_formatos(self):
        o1 = self.crear_orden("en_camino")
        o2 = self.crear_orden("en_camino")
        for texto in (o2.codigo, str(o2.pk), f"{o2.pk:04d}", o2.codigo.lower()):
            r = self.cargar(q=texto)
            ordenes = {e["orden"].pk for d in r.context["dias"] for e in d["eventos"]}
            self.assertEqual(ordenes, {o2.pk}, texto)
        self.assertEqual(self.cargar(q="sin numeros").context["dias"], [])

    def test_periodo_por_defecto_7_dias_y_todo(self):
        o = self.crear_orden("en_camino")
        HistorialEstado.objects.filter(orden=o).update(fecha=timezone.now() - timedelta(days=20))
        self.assertEqual(self.cargar().context["n_eventos"], 0)
        self.assertEqual(self.cargar(rango="30").context["n_eventos"], 1)
        self.assertEqual(self.cargar(rango="todo").context["n_eventos"], 1)
        self.assertEqual(self.cargar(rango="hoy").context["n_eventos"], 0)

    def test_agrupa_por_dias_con_etiqueta_ayer(self):
        o = self.crear_orden("en_camino")
        HistorialEstado.objects.filter(orden=o).update(fecha=timezone.now() - timedelta(days=1))
        self.crear_orden("en_camino")
        etiquetas = [d["etiqueta"] for d in self.cargar().context["dias"]]
        self.assertEqual(etiquetas, ["Hoy", "Ayer"])

    def test_paginacion(self):
        for _ in range(16):
            self.crear_orden("en_camino")                              # 16 órdenes → 16 eventos
        # reducimos el tamaño de página para no crear cientos de filas
        from . import views_actividad
        original = views_actividad.POR_PAGINA
        views_actividad.POR_PAGINA = 10
        try:
            r1 = self.cargar()
            self.assertEqual(r1.context["n_eventos"], 10)
            self.assertTrue(r1.context["hay_siguiente"])
            r2 = self.cargar(pagina=2)
            self.assertEqual(r2.context["n_eventos"], 6)
            self.assertFalse(r2.context["hay_siguiente"])
            self.assertContains(r2, "Anterior")
            vistos = {e["fecha"] for d in r1.context["dias"] + r2.context["dias"] for e in d["eventos"]}
            self.assertEqual(len(vistos), 16)
        finally:
            views_actividad.POR_PAGINA = original

    def test_parametros_basura_no_rompen(self):
        r = self.cargar(tipo="x", rango="y", usuario="abc", pagina="zzz")
        self.assertEqual(r.status_code, 200)

    def test_consultas_constantes(self):
        self.orden_con_incidencias()
        self.cargar()
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse("actividad"))
        for _ in range(3):
            self.orden_con_incidencias()
        self.entrar("admin")
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(reverse("actividad"))
        self.assertEqual(len(pocas), len(muchas))
