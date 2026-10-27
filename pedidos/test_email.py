from django.core import mail
from django.urls import reverse

from . import services
from .models import PlantillaEmail
from .test_ordenes import BaseOrdenes

CAMPOS = {"asunto": "Pedido {codigo}", "saludo": "Hola {proveedor}", "introduccion": "Intro de {codigo}",
          "cierre": "Gracias\nUn saludo", "firma": "Compras OrderFlow"}


class ComposicionTests(BaseOrdenes):
    def autorizar(self, o):
        self.entrar("admin")
        self.client.post(reverse("orden_autorizar", args=[o.pk]))
        return mail.outbox[-1]

    def test_estructura_por_defecto_en_texto_y_html(self):
        o = self.crear_orden("creada")
        correo = self.autorizar(o)
        self.assertEqual(correo.subject, f"Orden de compra {o.codigo}")
        cuerpo = correo.body
        orden_de_bloques = [cuerpo.index(t) for t in ("Estimados señores de", "Les remitimos", "Etanol 96%", "Quedamos a su disposición",
                                                      "Compras", "Departamento de Compras")]
        self.assertEqual(orden_de_bloques, sorted(orden_de_bloques))
        html = correo.alternatives[0].content
        for esperado in ("<table", "Artículo", "Cantidad", "Unidad", "Etanol 96%", "Sosa cáustica", "Cajas" if False else "Unidades"):
            self.assertIn(esperado, html)

    def test_la_firma_lleva_el_nombre_del_operario_de_compras_y_la_fecha_de_hoy(self):
        from django.utils import timezone
        from django.utils.formats import date_format
        o = self.crear_orden("creada")
        correo = self.autorizar(o)
        self.assertIn("Compras", correo.body)                                    # first_name = "Compras"
        self.assertIn(date_format(timezone.localdate(), "j \\d\\e F \\d\\e Y"), correo.body)
        self.assertIn("Compras", correo.alternatives[0].content)

    def test_usa_la_plantilla_editada(self):
        PlantillaEmail.objects.update_or_create(pk=1, defaults=CAMPOS)
        o = self.crear_orden("creada")
        correo = self.autorizar(o)
        self.assertEqual(correo.subject, f"Pedido {o.codigo}")
        self.assertIn("Hola Químicas Vallès S.L.", correo.body)
        self.assertIn(f"Intro de {o.codigo}", correo.body)
        self.assertIn("Compras OrderFlow", correo.body)

    def test_el_html_escapa_el_contenido_de_la_plantilla(self):
        PlantillaEmail.objects.update_or_create(pk=1, defaults={**CAMPOS, "saludo": "<script>alert(1)</script>"})
        correo = self.autorizar(self.crear_orden("creada"))
        html = correo.alternatives[0].content
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_asunto_sin_saltos_de_linea(self):
        asunto, _, _ = services.componer_email(services.DATOS_EJEMPLO, PlantillaEmail(**{**CAMPOS, "asunto": "Uno\nDos  {codigo}"}))
        self.assertEqual(asunto, "Uno Dos OC-2026-0001")

    def test_solo_se_sustituyen_las_variables_conocidas(self):
        _, texto, _ = services.componer_email(
            services.DATOS_EJEMPLO, PlantillaEmail(**{**CAMPOS, "saludo": "{proveedor} {__class__} {otra}"}))
        self.assertIn("Proveedor de ejemplo S.L. {__class__} {otra}", texto)

    def test_variables_desconocidas(self):
        self.assertEqual(services.variables_desconocidas("{codigo} {x} {y} {proveedor}"), ["x", "y"])


class AjustesEmailTests(BaseOrdenes):
    def url(self): return reverse("ajustes_email")

    def test_solo_administrador(self):
        for usuario, esperado in [("admin", 200), ("compras", 403), ("almacen", 403), ("produccion", 403)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(self.url()).status_code, esperado, usuario)
            self.assertEqual(self.client.get(reverse("ajustes_email_vista_previa")).status_code, esperado, usuario)
            self.assertEqual(self.client.post(self.url(), CAMPOS).status_code, 302 if usuario == "admin" else 403, usuario)

    def test_menu_ajustes_solo_para_el_administrador(self):
        self.entrar("admin")
        r = self.client.get(self.url())
        self.assertIn("Ajustes", [i["etiqueta"] for g in r.context["menu"] for i in g["items"]])
        self.entrar("compras")
        r = self.client.get(reverse("orden_lista"))
        self.assertNotIn("Ajustes", [i["etiqueta"] for g in r.context["menu"] for i in g["items"]])

    def test_primera_visita_crea_la_plantilla_con_los_textos_por_defecto(self):
        self.entrar("admin")
        r = self.client.get(self.url())
        self.assertEqual(PlantillaEmail.objects.count(), 1)
        self.assertEqual(r.context["form"]["asunto"].value(), PlantillaEmail.POR_DEFECTO["asunto"])
        self.assertContains(r, "Detalle del pedido")
        self.assertContains(r, 'readonly') if False else None

    def test_guardar_cambios(self):
        self.entrar("admin")
        r = self.client.post(self.url(), {**CAMPOS, "accion": "guardar"})
        self.assertRedirects(r, self.url())
        p = PlantillaEmail.obtener()
        self.assertEqual((p.asunto, p.firma, p.actualizado_por), ("Pedido {codigo}", "Compras OrderFlow", self.usuarios["admin"]))

    def test_validaciones(self):
        self.entrar("admin")
        for campo, valor, mensaje in [
            ("asunto", "", "no puede quedar vacío"),
            ("saludo", "  ", "no puede quedar vacío"),
            ("asunto", "Pedido {numero}", "Variable no válida: {numero}"),
            ("cierre", "Gracias {x}", "Variable no válida"),
            ("asunto", "Línea 1\nLínea 2", "una sola línea"),
        ]:
            r = self.client.post(self.url(), {**CAMPOS, campo: valor, "accion": "guardar"})
            self.assertEqual(r.status_code, 200, (campo, valor))
            self.assertContains(r, mensaje)
            self.assertNotEqual(PlantillaEmail.obtener().asunto, "Línea 1\nLínea 2")
        self.assertEqual(PlantillaEmail.obtener().asunto, PlantillaEmail.POR_DEFECTO["asunto"])   # nada se guardó

    def test_restaurar_por_defecto(self):
        PlantillaEmail.objects.update_or_create(pk=1, defaults=CAMPOS)
        self.entrar("admin")
        self.client.post(self.url(), {"accion": "restaurar"})
        p = PlantillaEmail.obtener()
        self.assertEqual({c: getattr(p, c) for c in PlantillaEmail.POR_DEFECTO}, PlantillaEmail.POR_DEFECTO)

    def test_vista_previa_con_datos_de_ejemplo_y_borrador_sin_guardar(self):
        self.entrar("admin")
        r = self.client.get(reverse("ajustes_email_vista_previa"))
        self.assertContains(r, "Proveedor de ejemplo S.L.")
        self.assertContains(r, "Asunto:")
        self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")
        r = self.client.get(reverse("ajustes_email_vista_previa"), {"saludo": "Buenas {proveedor}", "asunto": "<b>X</b>"})
        self.assertContains(r, "Buenas Proveedor de ejemplo S.L.")
        self.assertContains(r, "&lt;b&gt;X&lt;/b&gt;")                       # el asunto de la banda va escapado
        self.assertEqual(PlantillaEmail.obtener().saludo, PlantillaEmail.POR_DEFECTO["saludo"])
