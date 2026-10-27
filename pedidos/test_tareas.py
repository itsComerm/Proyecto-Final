from decimal import Decimal

from django.core import mail
from django.urls import reverse

from . import services
from .models import Incidencia, OrdenCompra, Tarea
from .test_incidencias import BaseIncidencias
from .test_recepcion import post_recepcion


class BaseTareas(BaseIncidencias):
    def abrir(self, categoria="otra", extra=None, linea=1, estado="en_camino"):
        """Almacén verifica la orden y marca NG la línea indicada con los campos de su categoría."""
        o = self.crear_orden(estado)
        lineas = list(o.lineas.order_by("pk"))
        datos = post_recepcion(lineas, [("ng", "Algo ha pasado") if i == linea else ("ok",) for i in range(len(lineas))])
        pk = lineas[linea].pk
        datos[f"cat_{pk}"] = categoria
        datos.update({k.replace("#", str(pk)): (v(lineas[linea]) if callable(v) else v) for k, v in (extra or {}).items()})
        self.entrar("almacen")
        r = self.client.post(reverse("recepcion_verificar", args=[o.pk]), datos)
        return o, lineas[linea], r

    def menu(self, usuario, etiqueta):
        self.entrar(usuario)
        r = self.client.get(reverse("orden_lista") if usuario != "almacen" else reverse("recepcion_lista"))
        return [i for g in r.context["menu"] for i in g["items"] if i["etiqueta"] == etiqueta][0]


class AperturaPorCategoriaTests(BaseTareas):
    def test_cantidad_calcula_la_diferencia_y_el_sentido(self):
        o, linea, _ = self.abrir("cantidad", {"cant_#": lambda l: str(l.cantidad_pedida - 1)})
        linea.refresh_from_db()
        inc = Incidencia.objects.get()
        self.assertEqual((inc.categoria, inc.subtipo, inc.unidades_afectadas), ("cantidad", "faltan", Decimal("1")))
        self.assertEqual(linea.cantidad_verificada, linea.cantidad_pedida - 1)
        self.assertIn("faltan", inc.descripcion)
        o2, linea2, _ = self.abrir("cantidad", {"cant_#": lambda l: str(l.cantidad_pedida + 5)})
        self.assertEqual(Incidencia.objects.get(linea=linea2).subtipo, "sobran")
        self.assertEqual(Incidencia.objects.get(linea=linea2).unidades_afectadas, Decimal("5"))

    def test_cantidad_exige_un_valor_distinto_de_lo_pedido(self):
        o = self.crear_orden("en_camino")
        pedida = o.lineas.order_by("pk")[1].cantidad_pedida
        for valor, aviso in (("", "Indica cuántas unidades"), (str(pedida), "Coincide con lo pedido")):
            o, _, r = self.abrir("cantidad", {"cant_#": valor})
            self.assertContains(r, aviso)
            o.refresh_from_db()
            self.assertEqual(o.estado, "en_camino")
        self.assertEqual(Incidencia.objects.count(), 0)

    def test_producto_y_danado_piden_tipo_y_unidades(self):
        _, _, r = self.abrir("producto", {"afec_#": "2"})
        self.assertContains(r, "Elige el tipo de problema")
        _, _, r = self.abrir("danado", {"sub_#": "humedad"})
        self.assertContains(r, "Indica las unidades afectadas")
        _, _, r = self.abrir("danado", {"sub_#": "inventado", "afec_#": "1"})
        self.assertContains(r, "Elige el tipo de problema")
        self.assertEqual(Incidencia.objects.count(), 0)
        self.abrir("producto", {"sub_#": "color", "afec_#": "2"})
        inc = Incidencia.objects.get()
        self.assertEqual((inc.categoria, inc.subtipo, inc.unidades_afectadas), ("producto", "color", Decimal("2")))
        self.assertIn("Color", inc.descripcion)

    def test_documentacion_y_otra(self):
        _, _, r = self.abrir("documentacion")
        self.assertContains(r, "Elige el tipo de problema")
        self.abrir("documentacion", {"sub_#": "albaran"})
        self.assertEqual(Incidencia.objects.get().subtipo, "albaran")
        _, _, r = self.abrir("otra", {"desc_#": " "})
        self.assertContains(r, "Describe el problema")

    def test_sin_categoria_no_se_abre(self):
        _, _, r = self.abrir("")
        self.assertContains(r, "Elige la categoría de la incidencia")
        self.assertEqual(Incidencia.objects.count(), 0)

    def test_el_servicio_no_confia_en_el_formulario(self):
        o = self.crear_orden("en_camino")
        l1, l2 = list(o.lineas.order_by("pk"))
        with self.assertRaises(ValueError):
            services.registrar_recepcion(o, self.usuarios["almacen"], {l1.pk: {"resultado": "ok"},
                                         l2.pk: {"resultado": "ng", "categoria": "cantidad", "cantidad": l2.cantidad_pedida}})
        o.refresh_from_db()
        self.assertEqual(o.estado, "en_camino")


class ReposicionTests(BaseTareas):
    def incidencia_con_reposicion(self):
        o, linea, _ = self.abrir("cantidad", {"cant_#": lambda l: str(l.cantidad_pedida - 1)})
        inc = Incidencia.objects.get(linea=linea)
        self.paso("compras", inc, "proponer", resolucion="reponer", unidades_reposicion="7",
                  dep_0="Compras", acc_0="avisar_reponer", txt_0="Pedir 7 uds. urgentes")
        return o, linea, inc

    def test_la_orden_de_reposicion_nace_al_aprobar_sin_email(self):
        o, linea, inc = self.incidencia_con_reposicion()
        self.assertEqual(OrdenCompra.objects.filter(tipo="incidencia").count(), 0)      # aún no
        mail.outbox.clear()
        self.paso("admin", inc, "aceptar", texto="")
        rep = OrdenCompra.objects.get(tipo="incidencia")
        self.assertEqual((rep.estado, rep.origen, rep.creado_por), ("en_camino", inc, self.usuarios["admin"]))
        l = rep.lineas.get()
        self.assertEqual((l.producto, l.unidad_medida, l.cantidad_pedida), (linea.producto, linea.unidad_medida, Decimal("7")))
        self.assertEqual(mail.outbox, [])
        self.assertEqual(rep.historial.get().estado_nuevo, "en_camino")
        auto = inc.tareas.get(automatica=True)
        self.assertEqual((auto.departamento, auto.accion, auto.estado, auto.orden_reposicion), ("Almacén", "recepcionar_reposicion", "pendiente", rep))

    def test_la_reposicion_aparece_en_recepcion_con_su_origen(self):
        o, _, inc = self.incidencia_con_reposicion()
        self.paso("admin", inc, "aceptar", texto="")
        rep = OrdenCompra.objects.get(tipo="incidencia")
        self.entrar("almacen")
        r = self.client.get(reverse("recepcion_lista"))
        self.assertContains(r, rep.codigo)
        self.assertContains(r, f"Reposición de {o.codigo}")

    def test_la_tarea_de_recepcion_no_se_completa_a_mano_y_se_cierra_sola(self):
        o, _, inc = self.incidencia_con_reposicion()
        self.paso("admin", inc, "aceptar", texto="")
        auto = inc.tareas.get(automatica=True)
        self.entrar("almacen")
        self.client.post(reverse("tarea_completar", args=[auto.pk]))
        auto.refresh_from_db()
        self.assertEqual(auto.estado, "pendiente")
        # Compras hace lo suyo: queda solo esperar la mercancía
        self.completar(inc)
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "reposicion")
        self.assertEqual(services.rol_que_debe_actuar(inc), "Almacén")
        self.entrar("almacen")
        self.assertEqual(self.client.get(reverse("incidencia_lista")).context["n_accion"], 1)
        # Almacén recibe la orden de reposición
        rep = OrdenCompra.objects.get(tipo="incidencia")
        linea = rep.lineas.get()
        self.entrar("almacen")
        self.client.post(reverse("recepcion_verificar", args=[rep.pk]), post_recepcion([linea], [("ok",)]))
        auto.refresh_from_db(); inc.refresh_from_db(); rep.refresh_from_db()
        self.assertEqual((auto.estado, auto.completada_por, rep.estado), ("completada", self.usuarios["almacen"], "recibido"))
        self.assertEqual(inc.fase, "solucionada")
        self.assertTrue(inc.mensajes.filter(tipo="tarea", texto__contains="Reposición recibida").exists())
        # el Administrador verifica y cierra: la orden original pasa a recibido
        self.paso("admin", inc, "cerrar", texto="")
        o.refresh_from_db(); inc.refresh_from_db()
        self.assertEqual((inc.fase, o.estado), ("resuelta", "recibido"))

    def test_si_la_reposicion_llega_mal_se_abre_otra_incidencia_sobre_ella(self):
        _, _, inc = self.incidencia_con_reposicion()
        self.paso("admin", inc, "aceptar", texto="")
        rep = OrdenCompra.objects.get(tipo="incidencia")
        self.entrar("almacen")
        datos = post_recepcion([rep.lineas.get()], [("ng", "Otra vez rotas")])
        self.client.post(reverse("recepcion_verificar", args=[rep.pk]), datos)
        rep.refresh_from_db()
        self.assertEqual(rep.estado, "incidencia")
        self.assertEqual(Incidencia.objects.filter(linea__orden=rep).count(), 1)
        self.assertEqual(inc.tareas.get(automatica=True).estado, "completada")        # la mercancía llegó, aunque mal

    def test_la_reposicion_pide_unidades_validas(self):
        o, linea, _ = self.abrir("cantidad", {"cant_#": lambda l: str(l.cantidad_pedida - 1)})
        inc = Incidencia.objects.get(linea=linea)
        self.paso("compras", inc, "proponer", resolucion="reponer", unidades_reposicion="0",
                  dep_0="Compras", acc_0="avisar_reponer")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "abierta")


class AceptarSinTareasTests(BaseTareas):
    def test_aceptar_el_material_se_cierra_al_aprobar(self):
        o, linea, _ = self.abrir("cantidad", {"cant_#": lambda l: str(l.cantidad_pedida + 5)})
        inc = Incidencia.objects.get(linea=linea)
        self.assertEqual(inc.subtipo, "sobran")
        self.paso("compras", inc, "proponer", resolucion="aceptar")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "propuesta")
        r = self.paso("admin", inc, "aceptar", texto="Nos vale")
        inc.refresh_from_db(); o.refresh_from_db()
        self.assertEqual((inc.fase, inc.resuelta, o.estado), ("resuelta", True, "recibido"))
        self.assertContains(self.client.get(r["Location"]), "queda cerrada")

    def test_otras_resoluciones_exigen_tareas_y_deben_encajar_con_la_categoria(self):
        o, linea, _ = self.abrir("cantidad", {"cant_#": lambda l: str(l.cantidad_pedida - 1)})                      # faltan unidades
        inc = Incidencia.objects.get(linea=linea)
        r = self.paso("compras", inc, "proponer", resolucion="devolver_abono", dep_0="Almacén", acc_0="otra", txt_0="x")
        self.assertEqual(r.status_code, 200)                                       # «devolver» no vale para «faltan»
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "abierta")


class TareasPorDepartamentoTests(BaseTareas):
    def con_tareas(self):
        o, linea, _ = self.abrir("producto", {"sub_#": "color", "afec_#": "2"})
        inc = Incidencia.objects.get(linea=linea)
        self.paso("compras", inc, "proponer", resolucion="devolver_abono",
                  dep_0="Almacén", acc_0="preparar_devolucion", txt_0="Muelle 2",
                  dep_1="Compras", acc_1="solicitar_abono",
                  dep_2="Producción", acc_2="confirmar_valido",
                  dep_3="Administrador", acc_3="autorizar_descuento")
        self.paso("admin", inc, "aceptar", texto="")
        return o, inc

    def test_cada_departamento_ve_solo_las_suyas(self):
        _, inc = self.con_tareas()
        esperado = {"almacen": "Preparar la devolución", "compras": "Solicitar abono o nota de crédito", "produccion": "Confirmar si el material es válido",
                    "admin": "Autorizar un descuento"}
        for usuario, texto in esperado.items():
            self.entrar(usuario)
            r = self.client.get(reverse("tarea_lista"))
            self.assertEqual(r.status_code, 200, usuario)
            self.assertEqual([t.etiqueta for t in r.context["tareas"]], [texto], usuario)
            self.assertEqual(self.menu(usuario, "Tareas")["contador"], 1, usuario)

    def test_los_borradores_y_las_de_otros_no_se_ven_ni_se_completan(self):
        o, linea, _ = self.abrir("producto", {"sub_#": "color", "afec_#": "2"})
        inc = Incidencia.objects.get(linea=linea)
        self.paso("compras", inc, "proponer", resolucion="devolver_abono", dep_0="Almacén", acc_0="otra", txt_0="x")
        self.assertEqual(self.menu("almacen", "Tareas")["contador"], 0)             # sin aprobar, nadie la ve
        self.paso("admin", inc, "aceptar", texto="")
        t = inc.tareas.get()
        self.entrar("compras")
        self.assertEqual(self.client.post(reverse("tarea_completar", args=[t.pk])).status_code, 403)

    def test_completadas_se_filtran_y_la_lista_pide_login(self):
        _, inc = self.con_tareas()
        self.completar(inc)                                                         # la de Almacén (primera pendiente)
        self.entrar("almacen")
        r = self.client.get(reverse("tarea_lista"), {"vista": "completadas"})
        self.assertEqual(len(r.context["tareas"]), 1)
        self.assertEqual(len(self.client.get(reverse("tarea_lista")).context["tareas"]), 0)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("tarea_lista")).status_code, 302)

    def test_la_lista_de_tareas_tiene_consultas_constantes(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.con_tareas()
        self.entrar("almacen")
        self.client.get(reverse("tarea_lista"))                                      # calienta
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse("tarea_lista"))
        for _ in range(3):
            self.con_tareas()
        self.entrar("almacen")
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(reverse("tarea_lista"))
        self.assertEqual(len(pocas), len(muchas))


class AvisoDeNovedadesTests(BaseTareas):
    def test_el_aviso_salta_aunque_el_contador_no_cambie(self):
        o1, l1, _ = self.abrir("producto", {"sub_#": "color", "afec_#": "1"})
        a = Incidencia.objects.get(linea=l1)
        self.paso("compras", a, "proponer", resolucion="devolver_abono", dep_0="Almacén", acc_0="otra", txt_0="Primera")
        self.paso("admin", a, "aceptar", texto="")
        # Almacén abre su lista de tareas: el aviso se apaga
        self.assertTrue(self.menu("almacen", "Tareas")["novedad"])
        self.client.get(reverse("tarea_lista"))
        item = self.menu("almacen", "Tareas")
        self.assertEqual((item["contador"], item["novedad"]), (1, False))
        # entra otra tarea y se completa la anterior: el número sigue siendo 1, pero el aviso vuelve
        o2, l2, _ = self.abrir("producto", {"sub_#": "color", "afec_#": "1"})
        b = Incidencia.objects.get(linea=l2)
        self.paso("compras", b, "proponer", resolucion="devolver_abono", dep_0="Almacén", acc_0="otra", txt_0="Segunda")
        self.paso("admin", b, "aceptar", texto="")
        self.completar(a)
        item = self.menu("almacen", "Tareas")
        self.assertEqual((item["contador"], item["novedad"]), (1, True))

    def test_una_incidencia_nueva_enciende_el_aviso_a_los_demas_y_abrir_la_lista_lo_apaga(self):
        self.menu("compras", "Incidencias")
        self.client.get(reverse("incidencia_lista"))
        self.assertFalse(self.menu("compras", "Incidencias")["novedad"])
        self.abrir("otra", {"desc_#": "Nueva incidencia"})
        self.assertTrue(self.menu("compras", "Incidencias")["novedad"])
        self.assertTrue(self.menu("admin", "Incidencias")["novedad"])
        self.assertFalse(self.menu("almacen", "Incidencias")["novedad"])           # la abrió Almacén
        self.entrar("compras")
        self.client.get(reverse("incidencia_lista"))
        self.assertFalse(self.menu("compras", "Incidencias")["novedad"])

    def test_el_menu_pinta_el_aviso(self):
        self.abrir("otra", {"desc_#": "Nueva incidencia"})
        self.entrar("compras")
        self.assertContains(self.client.get(reverse("orden_lista")), 'class="of-novedad"')


class AvisoSoloParaLosDemasTests(BaseTareas):
    def test_lo_que_creas_o_apruebas_tu_no_te_avisa(self):
        o, linea, _ = self.abrir("producto", {"sub_#": "color", "afec_#": "1"})
        inc = Incidencia.objects.get(linea=linea)
        # Compras se asigna una tarea a sí mismo y el Administrador la aprueba junto con otra para Administrador
        self.paso("compras", inc, "proponer", resolucion="devolver_abono",
                  dep_0="Compras", acc_0="solicitar_abono", dep_1="Administrador", acc_1="autorizar_descuento")
        self.menu("compras", "Tareas"); self.menu("administrador" if False else "admin", "Tareas")
        self.paso("admin", inc, "aceptar", texto="")
        self.assertFalse(self.menu("compras", "Tareas")["novedad"])      # la creó él mismo
        self.assertFalse(self.menu("admin", "Tareas")["novedad"])        # la aprobó él mismo
        self.assertEqual(self.menu("compras", "Tareas")["contador"], 1)  # el número sí cuenta
        self.entrar("compras")
        r = self.client.get(reverse("tarea_lista"))
        self.assertFalse(r.context["tareas"][0].nueva)

    def test_la_tarea_creada_por_otro_si_avisa(self):
        o, linea, _ = self.abrir("producto", {"sub_#": "color", "afec_#": "1"})
        inc = Incidencia.objects.get(linea=linea)
        self.paso("compras", inc, "proponer", resolucion="devolver_abono", dep_0="Almacén", acc_0="otra", txt_0="x")
        self.paso("admin", inc, "aceptar", texto="")
        self.assertTrue(self.menu("almacen", "Tareas")["novedad"])

    def test_quien_abre_la_incidencia_no_recibe_el_aviso_de_su_propia_incidencia(self):
        self.abrir("otra", {"desc_#": "La abro yo"})
        self.assertFalse(self.menu("almacen", "Incidencias")["novedad"])
        self.assertTrue(self.menu("compras", "Incidencias")["novedad"])


class RecomendacionesTests(BaseTareas):
    def test_todas_las_tarjetas_son_validas_para_su_categoria(self):
        from .catalogo_incidencias import ACCIONES_TAREA, presets_para, resoluciones_para
        casos = [("cantidad", "faltan"), ("cantidad", "sobran"), ("producto", "color"), ("danado", "humedad"),
                 ("documentacion", "albaran"), ("otra", "")]
        o = self.crear_orden("en_camino")
        linea = o.lineas.first()
        for categoria, sub in casos:
            inc = Incidencia(linea=linea, categoria=categoria, subtipo=sub, unidades_afectadas=Decimal("2"))
            for p in presets_para(inc):
                self.assertIn(p["resolucion"], resoluciones_para(categoria, sub), (categoria, sub, p["clave"]))
                for t in p["tareas"]:
                    self.assertIn(t["dep"], ACCIONES_TAREA[t["acc"]][1], (categoria, p["clave"], t))
                self.assertLessEqual(len(p["tareas"]), 4)

    def test_faltan_unidades_recomienda_reponer_y_abono(self):
        from .catalogo_incidencias import presets_para
        o, linea, _ = self.abrir("cantidad", {"cant_#": lambda l: str(l.cantidad_pedida - 1)})
        inc = Incidencia.objects.get(linea=linea)
        tarjetas = {p["clave"]: p for p in presets_para(inc)}
        self.assertEqual(set(tarjetas), {"reponer", "abono"})
        self.assertEqual([(t["dep"], t["acc"]) for t in tarjetas["reponer"]["tareas"]], [("Compras", "avisar_reponer")])
        self.assertTrue(tarjetas["reponer"]["repone"])      # Almacén recibirá la reposición con su tarea automática
        self.entrar("compras")
        r = self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        self.assertContains(r, "Soluciones recomendadas")
        self.assertContains(r, "Reponer lo que falta")
        self.assertContains(r, "data-preset=")

    def test_aceptar_siempre_da_a_almacen_la_tarea_de_ubicar_y_se_marca_a_mano(self):
        from .catalogo_incidencias import presets_para
        o, linea, _ = self.abrir("cantidad", {"cant_#": lambda l: str(l.cantidad_pedida + 5)})
        inc = Incidencia.objects.get(linea=linea)
        aceptar = next(p for p in presets_para(inc) if p["clave"] == "aceptar")
        self.assertEqual([(t["dep"], t["acc"]) for t in aceptar["tareas"]], [("Almacén", "ubicar_mercancia")])
        self.paso("compras", inc, "proponer", resolucion="aceptar", dep_0="Almacén", acc_0="ubicar_mercancia")
        self.paso("admin", inc, "aceptar", texto="")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "aceptada")                # no se cierra sola: Almacén tiene que ubicar
        self.completar(inc, "Ubicado en pasillo 3")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "solucionada")

    def test_los_productos_equivocados_o_danados_ofrecen_devolver_reponer_abono_y_aceptar(self):
        from .catalogo_incidencias import presets_para
        for categoria, extra in (("producto", {"sub_#": "color", "afec_#": "2"}), ("danado", {"sub_#": "envase", "afec_#": "2"})):
            o, linea, _ = self.abrir(categoria, extra)
            inc = Incidencia.objects.get(linea=linea)
            self.assertEqual([p["clave"] for p in presets_para(inc)], ["devolver_reponer", "devolver_abono", "aceptar"], categoria)

    def test_una_tarjeta_enviada_tal_cual_funciona_de_principio_a_fin(self):
        from .catalogo_incidencias import presets_para
        o, linea, _ = self.abrir("producto", {"sub_#": "color", "afec_#": "2"})
        inc = Incidencia.objects.get(linea=linea)
        p = next(p for p in presets_para(inc) if p["clave"] == "devolver_reponer")
        campos = {"resolucion": p["resolucion"]}
        for i, t in enumerate(p["tareas"]):
            campos.update({f"dep_{i}": t["dep"], f"acc_{i}": t["acc"], f"txt_{i}": t["texto"]})
        self.paso("compras", inc, "proponer", **campos)
        self.paso("admin", inc, "aceptar", texto="")
        inc.refresh_from_db()
        self.assertEqual((inc.fase, inc.tareas.count()), ("aceptada", 3))       # 2 de la tarjeta + recepción automática
        self.assertTrue(OrdenCompra.objects.filter(tipo="incidencia", origen=inc).exists())


class PersonalizadoTests(BaseTareas):
    def _incidencia(self, categoria="cantidad", extra=None):
        extra = extra if extra is not None else {"cant_#": lambda l: str(l.cantidad_pedida - 1)}
        _, linea, _ = self.abrir(categoria, extra)
        return Incidencia.objects.get(linea=linea)

    def test_la_ultima_opcion_es_personalizado_y_no_hay_lista_de_radios(self):
        inc = self._incidencia()
        self.entrar("compras")
        r = self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        self.assertContains(r, "Personalizado")
        self.assertNotContains(r, "Otra medida")
        self.assertNotContains(r, 'type="radio" name="proponer-resolucion"')
        self.assertContains(r, "Añadir nueva tarea")

    def test_personalizado_esta_disponible_en_todas_las_categorias(self):
        from .catalogo_incidencias import RESOLUCIONES, resoluciones_para
        for cat, sub in [("cantidad", "faltan"), ("cantidad", "sobran"), ("producto", "color"),
                         ("danado", "humedad"), ("documentacion", "albaran"), ("otra", "")]:
            opciones = resoluciones_para(cat, sub)
            self.assertIn("otra", opciones, (cat, sub))
            self.assertEqual(RESOLUCIONES["otra"][0], "Personalizado")

    def test_flujo_personalizado_manual_con_varias_tareas(self):
        inc = self._incidencia("documentacion", {"sub_#": "albaran"})
        self.paso("compras", inc, "proponer", resolucion="otra",
                  dep_0="Compras", acc_0="reclamar_documentacion", txt_0="Pedir el albarán",
                  dep_1="Almacén", acc_1="otra", txt_1="Archivar al recibirlo")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "propuesta")
        self.assertEqual(inc.tareas.filter(estado="borrador").count(), 2)

    def test_personalizado_sin_tareas_se_rechaza(self):
        inc = self._incidencia()
        self.paso("compras", inc, "proponer", resolucion="otra")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "abierta")

    def test_personalizado_con_reposicion_crea_la_orden_al_aprobar(self):
        inc = self._incidencia()
        self.paso("compras", inc, "proponer", resolucion="otra_reponer", unidades_reposicion="1",
                  dep_0="Compras", acc_0="avisar_reponer", txt_0="Avisar")
        inc.refresh_from_db()
        self.assertEqual(inc.resolucion, "otra_reponer")
        self.paso("admin", inc, "aceptar", texto="")
        self.assertTrue(OrdenCompra.objects.filter(tipo="incidencia", origen=inc).exists())
