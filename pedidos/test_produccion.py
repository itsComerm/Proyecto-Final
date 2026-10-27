from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import LineaPedido, Notificacion, Producto, Proveedor
from .test_incidencias import BaseIncidencias
from .test_recepcion import post_recepcion


class NotificacionesTests(BaseIncidencias):
    def avisos(self, usuario="produccion"):
        return Notificacion.objects.filter(destinatario=self.usuarios[usuario])

    def test_autorizar_ya_no_genera_avisos(self):
        o = self.crear_orden("creada", avisar=True)
        self.entrar("admin")
        self.client.post(reverse("orden_autorizar", args=[o.pk]))
        self.assertEqual(self.avisos().count(), 0)

    def test_sin_el_interruptor_no_se_avisa_a_nadie_en_ningun_momento(self):
        ok = self.crear_orden("en_camino")                               # avisar=False (por defecto)
        self.entrar("almacen")
        self.client.post(reverse("recepcion_verificar", args=[ok.pk]),
                         post_recepcion(list(ok.lineas.order_by("pk")), [("ok",), ("ok",)]))
        o, (inc,) = self.orden_con_incidencias()
        self.avanzar(inc, "solucionada"); self.paso("admin", inc, "cerrar", texto="")
        self.assertEqual(Notificacion.objects.count(), 0)

    def test_llegada_ok_y_llegada_con_incidencia(self):
        ok = self.crear_orden("en_camino", avisar=True)
        self.entrar("almacen")
        self.client.post(reverse("recepcion_verificar", args=[ok.pk]),
                         post_recepcion(list(ok.lineas.order_by("pk")), [("ok",), ("ok",)]))
        self.assertEqual([a.tipo for a in self.avisos()], ["recibida"])
        self.orden_con_incidencias(avisar=True)
        self.assertEqual(sorted(a.tipo for a in self.avisos()), ["con_incidencia", "recibida"])
        self.assertEqual(self.avisos("compras").count(), 0)

    def test_incidencia_resuelta_avisa_con_la_incidencia(self):
        o, (inc,) = self.orden_con_incidencias(avisar=True)
        self.avanzar(inc, "solucionada"); self.paso("admin", inc, "cerrar", texto="")
        aviso = self.avisos().get(tipo="incidencia_resuelta")
        self.assertEqual(aviso.incidencia_id, inc.pk)
        self.assertEqual(sorted(a.tipo for a in self.avisos()), ["con_incidencia", "incidencia_resuelta"])

    def test_cada_operario_de_produccion_recibe_el_suyo(self):
        from django.contrib.auth.models import Group, User
        otro = User.objects.create_user("produccion2")
        otro.groups.add(Group.objects.get(name="Producción"))
        self.orden_con_incidencias(avisar=True)
        self.assertEqual(Notificacion.objects.filter(tipo="con_incidencia").count(), 2)

    # ---- pantalla ----
    def crear_aviso(self, **kw):
        o = kw.pop("orden", None) or self.crear_orden("en_camino")
        return Notificacion.objects.create(destinatario=self.usuarios["produccion"], tipo="recibida", orden=o, **kw)

    def test_solo_produccion_ve_sus_notificaciones(self):
        for usuario, esperado in [("produccion", 200), ("compras", 403), ("almacen", 403), ("admin", 403)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse("notificacion_lista")).status_code, esperado, usuario)

    def test_lista_por_defecto_sin_leer_y_filtro_todas(self):
        a = self.crear_aviso(); b = self.crear_aviso(leida=True)
        self.entrar("produccion")
        self.assertEqual([x.pk for x in self.client.get(reverse("notificacion_lista")).context["avisos"]], [a.pk])
        r = self.client.get(reverse("notificacion_lista"), {"vista": "todas"})
        self.assertEqual({x.pk for x in r.context["avisos"]}, {a.pk, b.pk})
        self.assertEqual(self.client.get(reverse("notificacion_lista"), {"vista": "xx"}).context["vista"], "sin_leer")

    def test_marcar_leida_y_ver_orden(self):
        a = self.crear_aviso()
        self.entrar("produccion")
        r = self.client.post(reverse("notificacion_leer", args=[a.pk]), {"next": "orden"})
        self.assertRedirects(r, reverse("orden_detalle", args=[a.orden_id]))
        a.refresh_from_db(); self.assertTrue(a.leida)

    def test_no_se_puede_tocar_el_aviso_de_otro_usuario_ni_con_get(self):
        from django.contrib.auth.models import Group, User
        otro = User.objects.create_user("produccion2"); otro.groups.add(Group.objects.get(name="Producción"))
        ajeno = Notificacion.objects.create(destinatario=otro, tipo="recibida", orden=self.crear_orden("en_camino"))
        self.entrar("produccion")
        self.assertEqual(self.client.post(reverse("notificacion_leer", args=[ajeno.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("notificacion_leer", args=[ajeno.pk])).status_code, 405)
        ajeno.refresh_from_db(); self.assertFalse(ajeno.leida)

    def test_marcar_todas_solo_las_propias(self):
        from django.contrib.auth.models import Group, User
        otro = User.objects.create_user("produccion2"); otro.groups.add(Group.objects.get(name="Producción"))
        self.crear_aviso(); self.crear_aviso()
        ajeno = Notificacion.objects.create(destinatario=otro, tipo="recibida", orden=self.crear_orden("en_camino"))
        self.entrar("produccion")
        self.client.post(reverse("notificacion_leer_todas"))
        self.assertEqual(self.avisos().filter(leida=False).count(), 0)
        ajeno.refresh_from_db(); self.assertFalse(ajeno.leida)

    def test_contador_del_menu(self):
        self.entrar("produccion")
        def contador():
            r = self.client.get(reverse("dashboard_produccion"))
            return {i["etiqueta"]: i["contador"] for g in r.context["menu"] for i in g["items"]}["Notificaciones"]
        self.assertEqual(contador(), 0)
        self.crear_aviso(); self.crear_aviso(leida=True)
        self.assertEqual(contador(), 1)

    def test_otros_roles_no_tienen_notificaciones_en_el_menu(self):
        self.crear_aviso()
        for usuario in ("compras", "almacen", "admin"):
            self.entrar(usuario)
            r = self.client.get(reverse("orden_detalle", args=[self.crear_orden("en_camino").pk]))
            self.assertNotIn("Notificaciones", [i["etiqueta"] for g in r.context["menu"] for i in g["items"]], usuario)


class DashboardProduccionTests(BaseIncidencias):
    def cargar(self, usuario="produccion"):
        self.entrar(usuario)
        return self.client.get(reverse("dashboard_produccion"))

    def test_permisos_y_redireccion_de_inicio(self):
        for usuario, esperado in [("produccion", 200), ("compras", 403), ("almacen", 403), ("admin", 403)]:
            self.assertEqual(self.cargar(usuario).status_code, esperado, usuario)
        self.entrar("produccion")
        self.assertRedirects(self.client.get(reverse("inicio")), reverse("dashboard_produccion"))

    def test_base_vacia(self):
        r = self.cargar()
        self.assertContains(r, "No hay órdenes en camino")
        self.assertContains(r, "Todavía no ha llegado ninguna orden")
        self.assertContains(r, "Sin avisos todavía")

    def test_pendientes_y_llegadas_con_su_contenido(self):
        camino = self.crear_orden("en_camino")
        creada = self.crear_orden("creada")
        o, (inc,) = self.orden_con_incidencias()
        r = self.cargar()
        self.assertEqual([x.pk for x in r.context["en_camino"]], [camino.pk])
        self.assertEqual([x.pk for x in r.context["llegadas"]], [o.pk])
        self.assertNotContains(r, creada.codigo)
        self.assertContains(r, "Sosa cáustica")                 # el contenido de la orden está en la tarjeta
        self.assertContains(r, "Con incidencia")
        self.assertContains(r, "Recibida")

    def test_linea_con_incidencia_resuelta_se_distingue(self):
        o, (inc,) = self.orden_con_incidencias()
        self.avanzar(inc, "solucionada"); self.paso("admin", inc, "cerrar", texto="")
        r = self.cargar()
        self.assertContains(r, "Incidencia resuelta")
        self.assertEqual(r.context["llegadas"][0].resultado, "resuelta")

    def test_tarjetas_cuentan_correctamente(self):
        self.crear_orden("en_camino"); self.crear_orden("en_camino")
        self.orden_con_incidencias()
        Notificacion.objects.create(destinatario=self.usuarios["produccion"], tipo="recibida", orden=self.crear_orden("recibido"))
        t = self.cargar().context["t"]
        self.assertEqual((t["en_camino"], t["con_incidencia"]), (2, 1))
        self.assertGreaterEqual(t["sin_leer"], 1)

    def test_tarjetas_sin_novedades_blancas_y_con_novedades_destacadas(self):
        r = self.cargar()
        self.assertNotContains(r, "hay-novedad\"")
        self.assertEqual(r.content.decode().count("of-stat dash-card"), 3)
        self.assertNotContains(r, "Llegadas · últimos 7 días")
        self.orden_con_incidencias()
        self.entrar("produccion")
        r = self.client.get(reverse("dashboard_produccion"))
        self.assertContains(r, "1 con incidencia")
        self.assertContains(r, "Parte del material no está disponible hasta resolverla")
        self.assertContains(r, "hay-novedad")

    def test_consultas_constantes(self):
        def consultas():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(reverse("dashboard_produccion")).status_code, 200)
            return len(ctx)
        self.crear_orden("en_camino")
        self.orden_con_incidencias()
        self.entrar("produccion")
        pocas = consultas()
        for _ in range(4):
            self.crear_orden("en_camino")
        self.orden_con_incidencias()
        self.entrar("produccion")
        self.assertEqual(consultas(), pocas)


class MateriaPrimaTests(BaseIncidencias):
    def pagina(self, usuario="produccion", **params):
        self.entrar(usuario)
        return self.client.get(reverse("materia_prima"), params)

    def sugerencias(self, q, usuario="produccion", **params):
        self.entrar(usuario)
        return self.client.get(reverse("materia_prima_sugerencias"), {"q": q, **params})

    def recibir_ok(self):
        o = self.crear_orden("en_camino")
        self.entrar("almacen")
        self.client.post(reverse("recepcion_verificar", args=[o.pk]),
                         post_recepcion(list(o.lineas.order_by("pk")), [("ok",), ("ok",)]))
        return o

    def test_permisos(self):
        for usuario, esperado in [("produccion", 200), ("compras", 403), ("almacen", 403), ("admin", 403)]:
            self.assertEqual(self.pagina(usuario).status_code, esperado, usuario)
            self.assertEqual(self.sugerencias("e", usuario).status_code, esperado, usuario)

    def test_sin_busqueda_y_sin_resultados(self):
        self.assertContains(self.pagina(), "Escribe el nombre")
        self.assertContains(self.pagina(q="zzz"), "Sin resultados")

    def test_no_habla_de_stock_ni_de_en_almacen(self):
        self.recibir_ok()
        r = self.pagina(q="etanol", dias="todo")
        self.assertNotContains(r, "En almacén")
        self.assertContains(r, "no lleva control de stock")
        self.assertContains(r, "Recibida")

    def test_lista_todas_las_ordenes_que_contienen_el_producto(self):
        a = self.crear_orden("en_camino"); b = self.recibir_ok(); self.crear_orden("creada")
        r = self.pagina(producto=self.etanol.pk, dias="todo")
        self.assertEqual({l.orden_id for l in r.context["lineas"]}, {a.pk, b.pk})
        self.assertEqual(r.context["n_ordenes"], 2)
        self.assertTrue(all(l.producto_id == self.etanol.pk for l in r.context["lineas"]))

    def test_filtro_por_periodo_usa_la_fecha_de_llegada_o_salida(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import HistorialEstado
        vieja = self.crear_orden("en_camino"); reciente = self.crear_orden("en_camino")
        HistorialEstado.objects.filter(orden=vieja).update(fecha=timezone.now() - timedelta(days=20))
        for dias, esperadas in [("7", {reciente.pk}), ("30", {vieja.pk, reciente.pk}), ("todo", {vieja.pk, reciente.pk})]:
            r = self.pagina(q="etanol", dias=dias)
            self.assertEqual({l.orden_id for l in r.context["lineas"]}, esperadas, dias)
        HistorialEstado.objects.filter(orden=vieja).update(fecha=timezone.now() - timedelta(days=200))
        self.assertEqual({l.orden_id for l in self.pagina(q="etanol", dias="90").context["lineas"]}, {reciente.pk})

    def test_periodo_invalido_usa_30_dias(self):
        self.assertEqual(self.pagina(q="etanol", dias="xx").context["periodo"], "30")

    def test_busca_sin_distinguir_acentos_ni_mayusculas(self):
        acido = Producto.objects.create(nombre="Ácido cítrico", proveedor=self.etanol.proveedor)
        o = self.crear_orden("en_camino")
        LineaPedido.objects.create(orden=o, producto=acido, unidad_medida=self.unidades[0], cantidad_pedida="5")
        for q in ("acido", "ÁCIDO", "citrico"):
            self.assertEqual({p.pk for p in self.pagina(q=q, dias="todo").context["productos"]}, {acido.pk}, q)

    def test_no_incluye_ordenes_pendientes_de_autorizar(self):
        self.crear_orden("creada")
        r = self.pagina(q="etanol", dias="todo")
        self.assertEqual(r.context["lineas"], [])

    # ---- buscador en vivo (JSON) ----
    def test_sugerencias_devuelven_productos_con_su_numero_de_ordenes(self):
        self.crear_orden("en_camino"); self.recibir_ok(); self.crear_orden("creada")
        datos = self.sugerencias("etan", dias="todo").json()["resultados"]
        self.assertEqual(len(datos), 1)
        self.assertEqual((datos[0]["id"], datos[0]["ordenes"]), (self.etanol.pk, 2))
        self.assertIn("proveedor", datos[0])

    def test_sugerencias_respetan_el_periodo_y_la_consulta_vacia(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import HistorialEstado
        o = self.crear_orden("en_camino")
        HistorialEstado.objects.filter(orden=o).update(fecha=timezone.now() - timedelta(days=40))
        self.assertEqual(self.sugerencias("etanol", dias="7").json()["resultados"][0]["ordenes"], 0)
        self.assertEqual(self.sugerencias("etanol", dias="todo").json()["resultados"][0]["ordenes"], 1)
        self.assertEqual(self.sugerencias("").json(), {"resultados": []})
        self.assertEqual(self.sugerencias("zzz").json(), {"resultados": []})

    def test_sugerencias_maximo_ocho(self):
        for i in range(12):
            Producto.objects.create(nombre=f"Sal {i}", proveedor=self.etanol.proveedor)
        self.assertEqual(len(self.sugerencias("sal").json()["resultados"]), 8)

    def test_consultas_constantes(self):
        def consultas():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(reverse("materia_prima"), {"q": "etanol", "dias": "todo"}).status_code, 200)
            return len(ctx)
        self.crear_orden("en_camino")
        self.entrar("produccion"); pocas = consultas()
        for _ in range(5):
            self.crear_orden("en_camino")
        self.entrar("produccion")
        self.assertEqual(consultas(), pocas)
