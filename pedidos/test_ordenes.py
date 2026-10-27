from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from .models import HistorialEstado, LineaPedido, OrdenCompra, Producto, Proveedor, UnidadMedida


def datos_orden(proveedor, lineas, **extra):
    """Construye el POST de la pantalla «Nueva orden» (prefijo del formset: lineas)."""
    datos = {
        "proveedor": proveedor.pk,
        "lineas-TOTAL_FORMS": len(lineas),
        "lineas-INITIAL_FORMS": 0,
        "lineas-MIN_NUM_FORMS": 1,
        "lineas-MAX_NUM_FORMS": 1000,
    }
    for i, (producto, unidad, cantidad) in enumerate(lineas):
        datos[f"lineas-{i}-producto"] = producto.pk if producto else ""
        datos[f"lineas-{i}-unidad_medida"] = unidad.pk if unidad else ""
        datos[f"lineas-{i}-cantidad_pedida"] = cantidad
    datos.update(extra)
    return datos


class BaseOrdenes(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuarios = {}
        for username, rol in [("admin", "Administrador"), ("compras", "Compras"),
                              ("almacen", "Almacén"), ("produccion", "Producción")]:
            u = User.objects.create_user(username, first_name=username.capitalize())
            u.groups.add(Group.objects.get_or_create(name=rol)[0])
            cls.usuarios[username] = u
        # Las unidades las precarga la migración 0002: se reutilizan, no se vuelven a crear
        cls.unidades = [UnidadMedida.objects.get_or_create(nombre=n)[0] for n in ["Unidades", "Cajas", "Kg"]]
        cls.valles = Proveedor.objects.create(nombre="Químicas Vallès S.L.", email="pedidos@valles.example")
        cls.ebro = Proveedor.objects.create(nombre="Envases del Ebro", email="compras@ebro.example")
        cls.etanol = Producto.objects.create(nombre="Etanol 96%", proveedor=cls.valles)
        cls.sosa = Producto.objects.create(nombre="Sosa cáustica", proveedor=cls.valles)
        cls.cajas = Producto.objects.create(nombre="Cajas de cartón", proveedor=cls.ebro)

    def entrar(self, username):
        self.client.force_login(self.usuarios[username])

    def crear_orden(self, estado="creada", avisar=False):
        o = OrdenCompra.objects.create(creado_por=self.usuarios["compras"], estado=estado, notificar_produccion=avisar)
        LineaPedido.objects.create(orden=o, producto=self.etanol, unidad_medida=self.unidades[0], cantidad_pedida="10")
        LineaPedido.objects.create(orden=o, producto=self.sosa, unidad_medida=self.unidades[2], cantidad_pedida="2.5")
        HistorialEstado.objects.create(orden=o, estado_nuevo=estado, usuario=self.usuarios["compras"])
        return o


class PermisosOrdenesTests(BaseOrdenes):
    def test_anonimo_va_al_login(self):
        for nombre in ("orden_lista", "orden_nueva"):
            r = self.client.get(reverse(nombre))
            self.assertEqual(r.status_code, 302)
            self.assertIn("/login/", r["Location"])

    def test_listado_para_admin_compras_y_produccion_pero_no_para_almacen(self):
        o = self.crear_orden()
        for usuario, esperado in [("admin", 200), ("compras", 200), ("produccion", 200), ("almacen", 403)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse("orden_lista")).status_code, esperado, usuario)
        for usuario in ("admin", "compras"):
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse("orden_detalle", args=[o.pk])).status_code, 200, usuario)

    def test_almacen_y_produccion_no_ven_ordenes_pendientes_de_autorizar_pero_si_las_que_van_en_camino(self):
        creada = self.crear_orden()
        en_camino = self.crear_orden("en_camino")
        for usuario in ("almacen", "produccion"):
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse("orden_detalle", args=[creada.pk])).status_code, 404, usuario)
            self.assertEqual(self.client.get(reverse("orden_detalle", args=[en_camino.pk])).status_code, 200, usuario)

    def test_listado_de_produccion_excluye_las_creadas_y_su_filtro_no_ofrece_ese_estado(self):
        creada = self.crear_orden(); en_camino = self.crear_orden("en_camino")
        self.entrar("produccion")
        r = self.client.get(reverse("orden_lista"))
        self.assertEqual([o.pk for o in r.context["ordenes"]], [en_camino.pk])
        self.assertNotIn("creada", [e[0] for e in r.context["estados"]])
        self.entrar("compras")
        self.assertEqual(len(self.client.get(reverse("orden_lista")).context["ordenes"]), 2)

    def test_menu_de_almacen_no_tiene_ordenes_de_compra(self):
        self.entrar("almacen")
        r = self.client.get(reverse("recepcion_lista"))
        self.assertNotContains(r, 'href="%s"' % reverse("orden_lista"))
        self.entrar("compras")
        self.assertContains(self.client.get(reverse("orden_lista")), 'href="%s"' % reverse("orden_lista"))

    def test_solo_compras_y_administrador_crean_ordenes(self):
        for usuario, esperado in [("compras", 200), ("admin", 200), ("almacen", 403), ("produccion", 403)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse("orden_nueva")).status_code, esperado, usuario)

    def test_solo_administrador_autoriza(self):
        o = self.crear_orden()
        for usuario in ("compras", "almacen", "produccion"):
            self.entrar(usuario)
            r = self.client.post(reverse("orden_autorizar", args=[o.pk]))
            self.assertEqual(r.status_code, 403, usuario)
        o.refresh_from_db()
        self.assertEqual(o.estado, "creada")
        self.assertEqual(len(mail.outbox), 0)

    def test_autorizar_no_acepta_get(self):
        o = self.crear_orden()
        self.entrar("admin")
        self.assertEqual(self.client.get(reverse("orden_autorizar", args=[o.pk])).status_code, 405)

    def test_boton_autorizar_solo_para_administrador_y_orden_creada(self):
        o = self.crear_orden()
        url = reverse("orden_autorizar", args=[o.pk])
        self.entrar("admin")
        self.assertContains(self.client.get(reverse("orden_detalle", args=[o.pk])), url)
        self.entrar("compras")
        self.assertNotContains(self.client.get(reverse("orden_detalle", args=[o.pk])), url)

    def test_boton_nueva_orden_para_compras_y_administrador(self):
        url = reverse("orden_nueva")
        for usuario in ("compras", "admin"):
            self.entrar(usuario)
            self.assertContains(self.client.get(reverse("orden_lista")), url, msg_prefix=usuario)
        self.entrar("produccion")
        self.assertNotContains(self.client.get(reverse("orden_lista")), url)

    def test_orden_creada_por_el_administrador_queda_pendiente_y_firma_con_su_nombre(self):
        from django.core import mail
        self.entrar("admin")
        r = self.client.post(reverse("orden_nueva"), datos_orden(self.valles, [(self.etanol, self.unidades[0], "3")]))
        o = OrdenCompra.objects.latest("pk")
        self.assertRedirects(r, reverse("orden_detalle", args=[o.pk]))
        self.assertEqual((o.estado, o.creado_por), ("creada", self.usuarios["admin"]))
        self.assertEqual(len(mail.outbox), 0)                      # crear no envía nada: sigue haciendo falta autorizar
        self.client.post(reverse("orden_autorizar", args=[o.pk]))
        self.assertIn("Admin", mail.outbox[0].body)                # first_name del usuario de prueba

    def test_menu_ordenes_activo_para_los_roles_que_lo_ven(self):
        for usuario in (u for u in self.usuarios if u != "almacen"):
            self.entrar(usuario)
            r = self.client.get(reverse("orden_lista"))
            self.assertContains(r, 'href="%s" aria-current="page"' % reverse("orden_lista"))


class CrearOrdenTests(BaseOrdenes):
    def setUp(self):
        self.entrar("compras")

    def test_crea_orden_con_lineas_e_historial(self):
        u = self.unidades
        r = self.client.post(reverse("orden_nueva"), datos_orden(
            self.valles, [(self.etanol, u[0], "10"), (self.sosa, u[2], "2.5")]))
        orden = OrdenCompra.objects.get()
        self.assertRedirects(r, reverse("orden_detalle", args=[orden.pk]))
        self.assertEqual(orden.estado, "creada")
        self.assertEqual(orden.creado_por, self.usuarios["compras"])
        self.assertEqual(orden.lineas.count(), 2)
        self.assertEqual(orden.proveedor, self.valles)
        h = orden.historial.get()
        self.assertEqual((h.estado_anterior, h.estado_nuevo, h.usuario), ("", "creada", self.usuarios["compras"]))
        self.assertTrue(orden.codigo.startswith("OC-"))

    def test_por_defecto_no_se_avisa_a_nadie(self):
        self.client.post(reverse("orden_nueva"), datos_orden(self.valles, [(self.etanol, self.unidades[0], "1")]))
        o = OrdenCompra.objects.get()
        self.assertFalse(o.notificar_produccion)
        self.assertEqual(o.departamentos_avisados, [])

    def test_interruptor_activa_el_aviso_a_produccion(self):
        self.client.post(reverse("orden_nueva"), datos_orden(self.valles, [(self.etanol, self.unidades[0], "1")],
                                                              notificar_produccion="on"))
        o = OrdenCompra.objects.get()
        self.assertTrue(o.notificar_produccion)
        self.assertEqual(o.departamentos_avisados, ["Producción"])

    def test_el_formulario_muestra_un_unico_interruptor_apagado(self):
        r = self.client.get(reverse("orden_nueva"))
        self.assertContains(r, 'role="switch"')
        self.assertNotContains(r, "notificar_compras")
        self.assertNotContains(r, "notificar_administracion")
        self.assertNotContains(r, 'name="notificar_produccion" class="of-switch" role="switch" id="id_notificar_produccion" checked')

    def test_rechaza_producto_de_otro_proveedor(self):
        r = self.client.post(reverse("orden_nueva"), datos_orden(
            self.valles, [(self.etanol, self.unidades[0], "1"), (self.cajas, self.unidades[1], "5")]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "no pertenece al proveedor elegido")
        self.assertEqual(OrdenCompra.objects.count(), 0)
        self.assertEqual(LineaPedido.objects.count(), 0)

    def test_rechaza_producto_repetido(self):
        r = self.client.post(reverse("orden_nueva"), datos_orden(
            self.valles, [(self.etanol, self.unidades[0], "1"), (self.etanol, self.unidades[1], "2")]))
        self.assertContains(r, "ya está en la orden")
        self.assertEqual(OrdenCompra.objects.count(), 0)

    def test_rechaza_orden_sin_lineas(self):
        d = datos_orden(self.valles, [(None, None, "")])
        r = self.client.post(reverse("orden_nueva"), d)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(OrdenCompra.objects.count(), 0)

    def test_rechaza_cantidad_cero_o_negativa(self):
        for cantidad in ("0", "-3"):
            r = self.client.post(reverse("orden_nueva"), datos_orden(
                self.valles, [(self.etanol, self.unidades[0], cantidad)]))
            self.assertEqual(r.status_code, 200, cantidad)
        self.assertEqual(OrdenCompra.objects.count(), 0)

    def test_rechaza_sin_proveedor(self):
        d = datos_orden(self.valles, [(self.etanol, self.unidades[0], "1")])
        d["proveedor"] = ""
        r = self.client.post(reverse("orden_nueva"), d)
        self.assertContains(r, "Selecciona un proveedor.")
        self.assertEqual(OrdenCompra.objects.count(), 0)

    def test_linea_marcada_para_borrar_se_ignora(self):
        d = datos_orden(self.valles, [(self.etanol, self.unidades[0], "1"), (self.sosa, self.unidades[0], "3")])
        d["lineas-1-DELETE"] = "on"
        self.client.post(reverse("orden_nueva"), d)
        self.assertEqual(OrdenCompra.objects.get().lineas.count(), 1)

    def test_formulario_ofrece_data_proveedor_en_los_productos(self):
        r = self.client.get(reverse("orden_nueva"))
        self.assertContains(r, 'data-proveedor="%d"' % self.valles.pk)
        self.assertContains(r, 'data-proveedor="%d"' % self.ebro.pk)


class AutorizarOrdenTests(BaseOrdenes):
    def test_autoriza_registra_historial_y_envia_email(self):
        o = self.crear_orden()
        self.entrar("admin")
        r = self.client.post(reverse("orden_autorizar", args=[o.pk]), follow=True)
        o.refresh_from_db()
        self.assertEqual(o.estado, "en_camino")
        ultimo = o.historial.last()
        self.assertEqual((ultimo.estado_anterior, ultimo.estado_nuevo, ultimo.usuario),
                         ("creada", "en_camino", self.usuarios["admin"]))
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ["pedidos@valles.example"])
        self.assertIn(o.codigo, correo.subject)
        self.assertIn("Etanol 96%: 10 Unidades", correo.body)
        self.assertIn("Sosa cáustica: 2.5 Kg", correo.body)
        self.assertEqual(correo.alternatives[0].mimetype, "text/html")
        self.assertContains(r, "autorizada y enviada")

    def test_no_se_puede_autorizar_dos_veces(self):
        o = self.crear_orden()
        self.entrar("admin")
        self.client.post(reverse("orden_autorizar", args=[o.pk]))
        r = self.client.post(reverse("orden_autorizar", args=[o.pk]), follow=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(o.historial.count(), 2)
        self.assertContains(r, "Solo se pueden autorizar")

    def test_no_autoriza_una_orden_ya_en_camino(self):
        o = self.crear_orden(estado="en_camino")
        self.entrar("admin")
        self.client.post(reverse("orden_autorizar", args=[o.pk]))
        self.assertEqual(len(mail.outbox), 0)

    def test_si_falla_el_email_la_orden_queda_autorizada_con_aviso(self):
        from unittest import mock
        o = self.crear_orden()
        self.entrar("admin")
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp caído")):
            r = self.client.post(reverse("orden_autorizar", args=[o.pk]), follow=True)
        o.refresh_from_db()
        self.assertEqual(o.estado, "en_camino")
        self.assertContains(r, "no se pudo enviar el email")


class ListadoOrdenesTests(BaseOrdenes):
    def test_filtra_por_estado_y_proveedor(self):
        a = self.crear_orden("creada")
        b = self.crear_orden("en_camino")
        c = OrdenCompra.objects.create(creado_por=self.usuarios["compras"])
        LineaPedido.objects.create(orden=c, producto=self.cajas, unidad_medida=self.unidades[1], cantidad_pedida="4")
        self.entrar("compras")
        r = self.client.get(reverse("orden_lista"), {"estado": "en_camino"})
        self.assertEqual([o.pk for o in r.context["ordenes"]], [b.pk])
        r = self.client.get(reverse("orden_lista"), {"proveedor": self.ebro.pk})
        self.assertEqual([o.pk for o in r.context["ordenes"]], [c.pk])
        r = self.client.get(reverse("orden_lista"))
        self.assertEqual(len(r.context["ordenes"]), 3)

    def test_parametros_raros_no_rompen_el_listado(self):
        self.entrar("compras")
        r = self.client.get(reverse("orden_lista"), {"estado": "xx", "proveedor": "abc", "page": "9999"})
        self.assertIn(r.status_code, (200, 404))

    def test_paginacion(self):
        for _ in range(17):
            self.crear_orden()
        self.entrar("compras")
        r = self.client.get(reverse("orden_lista"))
        self.assertEqual(len(r.context["ordenes"]), 15)
        self.assertEqual(len(self.client.get(reverse("orden_lista"), {"page": 2}).context["ordenes"]), 2)

    def test_listado_no_hace_una_consulta_por_orden(self):
        """Con 3 o con 12 órdenes el número de consultas debe ser el mismo (sin N+1)."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def consultas():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(reverse("orden_lista")).status_code, 200)
            return len(ctx)

        self.entrar("produccion")
        for _ in range(3):
            self.crear_orden()
        con_3 = consultas()
        for _ in range(9):
            self.crear_orden()
        self.assertEqual(consultas(), con_3)


class ModeloOrdenTests(BaseOrdenes):
    def test_codigo_y_proveedor(self):
        o = self.crear_orden()
        self.assertRegex(o.codigo, r"^OC-\d{4}-%04d$" % o.pk)
        self.assertEqual(o.proveedor, self.valles)
        self.assertEqual(OrdenCompra().codigo, "OC-nueva")
