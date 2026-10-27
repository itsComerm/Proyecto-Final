from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import HistorialEstado, Incidencia, MensajeIncidencia, OrdenCompra
from .test_ordenes import BaseOrdenes
from .test_recepcion import post_recepcion

USUARIO_DE_ROL = {"Compras": "compras", "Almacén": "almacen", "Administrador": "admin", "Producción": "produccion"}


class BaseIncidencias(BaseOrdenes):
    def orden_con_incidencias(self, ng=(1,), avisar=False):
        """Orden en camino cuyas líneas indicadas (por posición) se marcan NG desde Almacén."""
        o = self.crear_orden("en_camino", avisar=avisar)
        lineas = list(o.lineas.order_by("pk"))
        resultados = [("ng", f"Problema {i}") if i in ng else ("ok",) for i in range(len(lineas))]
        self.entrar("almacen")
        self.client.post(reverse("recepcion_verificar", args=[o.pk]), post_recepcion(lineas, resultados))
        o.refresh_from_db()
        return o, list(Incidencia.objects.filter(linea__orden=o).order_by("pk"))

    def paso(self, usuario, inc, accion, **campos):
        """Un POST del ticket: los campos llevan el prefijo de la acción (p. ej. proponer-texto)."""
        self.entrar(usuario)
        return self.client.post(reverse("incidencia_accion", args=[inc.pk, accion]),
                                {f"{accion}-{k}": v for k, v in campos.items()})

    def proponer(self, inc, responsable="Almacén", texto="Reposición de las unidades dañadas", resolucion="otra"):
        """Compras propone: una resolución y una tarea de «otra acción» para el departamento indicado."""
        return self.paso("compras", inc, "proponer", resolucion=resolucion, dep_0=responsable, acc_0="otra", txt_0=texto)

    def completar(self, inc, texto=""):
        """El departamento de la primera tarea pendiente (no automática) la marca como completada."""
        tarea = inc.tareas.filter(estado="pendiente", automatica=False).first()
        self.entrar(USUARIO_DE_ROL[tarea.departamento])
        return self.client.post(reverse("tarea_completar", args=[tarea.pk]), {f"t{tarea.pk}-texto": texto})

    def avanzar(self, inc, hasta, responsable="Almacén"):
        """Lleva el ticket por el flujo normal hasta la fase indicada."""
        pasos = [("abierta", lambda: self.proponer(inc, responsable)),
                 ("propuesta", lambda: self.paso("admin", inc, "aceptar", texto="")),
                 ("aceptada", lambda: self.completar(inc, "Hecho")),
                 ("solucionada", lambda: self.paso("admin", inc, "cerrar", texto=""))]
        for fase, hacer in pasos:
            if fase == hasta:
                break
            hacer()
        inc.refresh_from_db()
        return inc


class PermisosTests(BaseIncidencias):
    def test_anonimo_va_al_login(self):
        _, (inc,) = self.orden_con_incidencias()
        self.client.logout()
        for url in (reverse("incidencia_lista"), reverse("incidencia_detalle", args=[inc.pk])):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertIn("/login/", r["Location"])
        r = self.client.post(reverse("incidencia_accion", args=[inc.pk, "comentar"]), {"comentar-texto": "x"})
        self.assertEqual(r.status_code, 302)

    def test_ven_admin_compras_y_almacen_pero_no_produccion(self):
        _, (inc,) = self.orden_con_incidencias()
        for usuario, esperado in [("admin", 200), ("compras", 200), ("almacen", 200), ("produccion", 403)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse("incidencia_lista")).status_code, esperado, usuario)
            self.assertEqual(self.client.get(reverse("incidencia_detalle", args=[inc.pk])).status_code, esperado, usuario)

    def test_produccion_no_puede_ni_comentar(self):
        _, (inc,) = self.orden_con_incidencias()
        r = self.paso("produccion", inc, "comentar", texto="hola")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(inc.mensajes.count(), 1)

    def test_las_acciones_solo_aceptan_post_y_conocidas(self):
        _, (inc,) = self.orden_con_incidencias()
        self.entrar("compras")
        self.assertEqual(self.client.get(reverse("incidencia_accion", args=[inc.pk, "proponer"])).status_code, 405)
        self.assertEqual(self.client.post(reverse("incidencia_accion", args=[inc.pk, "inventada"])).status_code, 403)

    def test_solo_compras_propone(self):
        _, (inc,) = self.orden_con_incidencias()
        for usuario in ("admin", "almacen"):
            self.assertEqual(self.proponer_como(usuario, inc).status_code, 403, usuario)
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "abierta")

    def proponer_como(self, usuario, inc):
        return self.paso(usuario, inc, "proponer", resolucion="otra", dep_0="Compras", acc_0="otra", txt_0="x")

    def test_solo_el_administrador_decide_y_cierra(self):
        _, (inc,) = self.orden_con_incidencias()
        self.proponer(inc)
        for accion in ("aceptar", "rechazar", "cerrar"):
            for usuario in ("compras", "almacen"):
                self.assertEqual(self.paso(usuario, inc, accion, texto="x").status_code, 403, f"{usuario}/{accion}")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "propuesta")

    def test_solo_el_departamento_asignado_completa_su_tarea(self):
        _, (inc,) = self.orden_con_incidencias()
        self.avanzar(inc, "aceptada", responsable="Almacén")
        tarea = inc.tareas.get()
        for usuario in ("compras", "admin", "produccion"):
            self.entrar(usuario)
            self.assertEqual(self.client.post(reverse("tarea_completar", args=[tarea.pk])).status_code, 403, usuario)
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "aceptada")
        self.completar(inc, "Recibida la reposición")
        inc.refresh_from_db(); tarea.refresh_from_db()
        self.assertEqual(inc.fase, "solucionada")
        self.assertEqual((tarea.estado, tarea.completada_por), ("completada", self.usuarios["almacen"]))


class FlujoTests(BaseIncidencias):
    def test_propuesta_guarda_resolucion_tareas_en_borrador_y_autor(self):
        _, (inc,) = self.orden_con_incidencias()
        r = self.proponer(inc, "Almacén", "  Abono del 20 %  ")
        self.assertRedirects(r, reverse("incidencia_detalle", args=[inc.pk]))
        inc.refresh_from_db()
        self.assertEqual((inc.fase, inc.resolucion), ("propuesta", "otra"))
        self.assertEqual(inc.gestionada_por, self.usuarios["compras"])
        t = inc.tareas.get()
        self.assertEqual((t.departamento, t.accion, t.texto, t.estado), ("Almacén", "otra", "Abono del 20 %", "borrador"))
        self.assertIsNone(t.visible_desde)                      # el departamento aún no la ve
        m = inc.mensajes.filter(tipo="propuesta").get()
        self.assertIn("Abono del 20 %", m.texto)
        self.assertIn("Almacén", m.texto)

    def test_propuesta_incompleta_se_rechaza_sin_cambios(self):
        _, (inc,) = self.orden_con_incidencias()
        self.entrar("compras")
        casos = [({"dep_0": "Compras", "acc_0": "otra", "txt_0": "x"}, "Elige una solución recomendada"),
                 ({"resolucion": "otra", "dep_0": "Compras", "txt_0": "x"}, "Elige la acción"),
                 ({"resolucion": "otra", "acc_0": "otra", "txt_0": "x"}, "Elige quién la hace"),
                 ({"resolucion": "otra", "dep_0": "Compras", "acc_0": "otra", "txt_0": " "}, "Describe la acción"),
                 ({"resolucion": "otra", "dep_0": "Compras", "acc_0": "preparar_devolucion"}, "Esa acción no la hace ese departamento"),
                 ({"resolucion": "devolver_reponer", "dep_0": "Compras", "acc_0": "otra", "txt_0": "x"}, "Elige una solución recomendada")]
        for campos, aviso in casos:
            r = self.client.post(reverse("incidencia_accion", args=[inc.pk, "proponer"]),
                                 {f"proponer-{k}": v for k, v in campos.items()})
            self.assertEqual(r.status_code, 200, aviso)
            self.assertContains(r, aviso)
        # sin ninguna tarea tampoco vale (salvo «Aceptar el material»)
        r = self.client.post(reverse("incidencia_accion", args=[inc.pk, "proponer"]), {"proponer-resolucion": "otra"})
        self.assertRedirects(r, reverse("incidencia_detalle", args=[inc.pk]))
        inc.refresh_from_db()
        self.assertEqual((inc.fase, inc.tareas.count()), ("abierta", 0))

    def test_no_se_puede_proponer_dos_veces(self):
        _, (inc,) = self.orden_con_incidencias()
        self.proponer(inc)
        r = self.proponer(inc, texto="Otra distinta")
        self.assertRedirects(r, reverse("incidencia_detalle", args=[inc.pk]))
        self.assertEqual(inc.mensajes.filter(tipo="propuesta").count(), 1)
        self.assertEqual(inc.tareas.count(), 1)

    def test_aprobar_publica_las_tareas_a_los_departamentos(self):
        _, (inc,) = self.orden_con_incidencias()
        self.proponer(inc)
        self.paso("admin", inc, "aceptar", texto="Adelante")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "aceptada")
        t = inc.tareas.get()
        self.assertEqual(t.estado, "pendiente")
        self.assertIsNotNone(t.visible_desde)
        m = inc.mensajes.filter(tipo="aceptacion").get()
        self.assertEqual((m.texto, m.autor, m.rol), ("Adelante", self.usuarios["admin"], "Administrador"))

    def test_rechazar_exige_motivo_descarta_las_tareas_y_devuelve_a_compras(self):
        _, (inc,) = self.orden_con_incidencias()
        self.proponer(inc)
        r = self.paso("admin", inc, "rechazar", texto="   ")
        self.assertContains(r, "Indica el motivo")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "propuesta")
        self.paso("admin", inc, "rechazar", texto="El abono no cubre el daño")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "abierta")
        self.assertEqual(inc.tareas.count(), 0)
        self.assertEqual(inc.mensajes.filter(tipo="rechazo").get().texto, "El abono no cubre el daño")
        self.proponer(inc, texto="Reposición completa")          # Compras puede volver a proponer
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "propuesta")
        self.assertEqual(inc.tareas.get().texto, "Reposición completa")

    def test_una_tarea_en_borrador_no_se_puede_completar(self):
        _, (inc,) = self.orden_con_incidencias()
        self.proponer(inc)
        tarea = inc.tareas.get()
        self.entrar("almacen")
        self.client.post(reverse("tarea_completar", args=[tarea.pk]))
        tarea.refresh_from_db()
        self.assertEqual(tarea.estado, "borrador")
        self.assertNotContains(self.client.get(reverse("tarea_lista")), "Reposición de las unidades dañadas")

    def test_varias_tareas_la_incidencia_sigue_abierta_hasta_completarlas_todas(self):
        _, (inc,) = self.orden_con_incidencias()
        self.paso("compras", inc, "proponer", resolucion="otra", dep_0="Almacén", acc_0="otra", txt_0="Preparar",
                  dep_1="Compras", acc_1="avisar_reponer", txt_1="")
        self.paso("admin", inc, "aceptar", texto="")
        self.assertEqual(inc.tareas.filter(estado="pendiente").count(), 2)
        for esperado in ("aceptada", "solucionada"):
            self.completar(inc)
            inc.refresh_from_db()
            self.assertEqual(inc.fase, esperado)

    def test_reabrir_una_tarea_exige_motivo_y_la_devuelve_a_su_departamento(self):
        _, (inc,) = self.orden_con_incidencias()
        self.avanzar(inc, "aceptada")
        self.completar(inc, "Recibida")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "solucionada")
        tarea = inc.tareas.get()
        self.entrar("admin")
        url = reverse("tarea_reabrir", args=[inc.pk, tarea.pk])
        self.client.post(url, {f"reabrir{tarea.pk}-texto": " "})
        tarea.refresh_from_db()
        self.assertEqual(tarea.estado, "completada")
        self.client.post(url, {f"reabrir{tarea.pk}-texto": "Faltan dos cajas"})
        tarea.refresh_from_db(); inc.refresh_from_db()
        self.assertEqual((tarea.estado, inc.fase), ("pendiente", "aceptada"))
        self.assertIn("Faltan dos cajas", inc.mensajes.filter(tipo="devolucion").get().texto)
        self.completar(inc, "Ya han llegado")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "solucionada")
        # solo el Administrador reabre
        self.entrar("compras")
        self.assertEqual(self.client.post(url, {f"reabrir{tarea.pk}-texto": "x"}).status_code, 403)

    def test_cerrar_resuelve_y_cierra_la_orden(self):
        o, (inc,) = self.orden_con_incidencias()
        self.avanzar(inc, "solucionada")
        r = self.paso("admin", inc, "cerrar", texto="Todo correcto", )
        inc.refresh_from_db(); o.refresh_from_db()
        self.assertEqual(inc.fase, "resuelta")
        self.assertTrue(inc.resuelta)
        self.assertEqual(inc.resuelta_por, self.usuarios["admin"])
        self.assertIsNotNone(inc.fecha_resolucion)
        self.assertEqual(o.estado, "recibido")
        h = HistorialEstado.objects.filter(orden=o).latest("pk")
        self.assertEqual((h.estado_anterior, h.estado_nuevo, h.usuario), ("incidencia", "recibido", self.usuarios["admin"]))
        self.assertContains(self.client.get(r["Location"]), "queda como Recibida")

    def test_con_dos_incidencias_la_orden_sigue_hasta_cerrar_la_ultima(self):
        o, (a, b) = self.orden_con_incidencias(ng=(0, 1))
        self.avanzar(a, "solucionada"); self.avanzar(b, "solucionada")
        self.paso("admin", a, "cerrar", texto="")
        o.refresh_from_db()
        self.assertEqual(o.estado, "incidencia")
        self.paso("admin", b, "cerrar", texto="")
        o.refresh_from_db()
        self.assertEqual(o.estado, "recibido")

    def test_no_se_cierra_antes_de_tiempo_ni_dos_veces(self):
        o, (inc,) = self.orden_con_incidencias()
        self.paso("admin", inc, "cerrar", texto="")                       # fase abierta
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "abierta")
        self.avanzar(inc, "solucionada")
        self.paso("admin", inc, "cerrar", texto="")
        n = HistorialEstado.objects.filter(orden=o).count()
        self.paso("admin", inc, "cerrar", texto="")
        self.assertEqual(HistorialEstado.objects.filter(orden=o).count(), n)

    def test_la_tarea_puede_ser_de_cualquier_departamento(self):
        for departamento in ("Compras", "Administrador", "Producción"):
            o, (inc,) = self.orden_con_incidencias()
            self.proponer(inc, departamento)
            self.paso("admin", inc, "aceptar", texto="")
            self.completar(inc)
            inc.refresh_from_db()
            self.assertEqual(inc.fase, "solucionada", departamento)
            self.assertEqual(inc.mensajes.filter(tipo="tarea").get().rol, departamento)

    def test_ciclo_completo_deja_un_hilo_ordenado(self):
        o, (inc,) = self.orden_con_incidencias()
        self.paso("almacen", inc, "comentar", texto="Adjunto el albarán con la rotura")
        self.avanzar(inc, "solucionada")
        self.paso("admin", inc, "cerrar", texto="")
        tipos = list(inc.mensajes.values_list("tipo", flat=True))
        self.assertEqual(tipos, ["apertura", "comentario", "propuesta", "aceptacion", "tarea", "cierre"])


class ComentariosTests(BaseIncidencias):
    def test_los_tres_roles_comentan(self):
        _, (inc,) = self.orden_con_incidencias()
        for usuario, rol in [("almacen", "Almacén"), ("compras", "Compras"), ("admin", "Administrador")]:
            self.paso(usuario, inc, "comentar", texto=f"Mensaje de {rol}")
            m = inc.mensajes.filter(tipo="comentario").latest("pk")
            self.assertEqual((m.texto, m.rol), (f"Mensaje de {rol}", rol))

    def test_comentario_vacio_no_se_guarda(self):
        _, (inc,) = self.orden_con_incidencias()
        r = self.paso("almacen", inc, "comentar", texto="   ")
        self.assertContains(r, "Escribe un mensaje")
        self.assertEqual(inc.mensajes.filter(tipo="comentario").count(), 0)

    def test_una_incidencia_resuelta_ya_no_admite_mensajes(self):
        _, (inc,) = self.orden_con_incidencias()
        self.avanzar(inc, "solucionada")
        self.paso("admin", inc, "cerrar", texto="")
        self.paso("almacen", inc, "comentar", texto="tarde")
        self.assertEqual(inc.mensajes.filter(tipo="comentario").count(), 0)
        self.entrar("almacen")
        self.assertNotContains(self.client.get(reverse("incidencia_detalle", args=[inc.pk])), "Publicar mensaje")


class PanelTests(BaseIncidencias):
    def test_cada_rol_ve_aviso_solo_cuando_le_toca(self):
        _, (inc,) = self.orden_con_incidencias()
        esperado = {"compras": True, "admin": False, "almacen": False}       # fase abierta → Compras
        for usuario, requiere in esperado.items():
            self.entrar(usuario)
            r = self.client.get(reverse("incidencia_lista"))
            self.assertEqual(r.context["n_accion"], 1 if requiere else 0, usuario)
            self.assertEqual(bool(r.context["incidencias"][0].requiere_mi_accion), requiere, usuario)
        self.proponer(inc, "Almacén")                                         # fase propuesta → Administrador
        self.entrar("admin")
        self.assertEqual(self.client.get(reverse("incidencia_lista")).context["n_accion"], 1)
        self.paso("admin", inc, "aceptar", texto="")                          # aceptada → responsable (Almacén)
        for usuario, n in [("almacen", 1), ("admin", 0), ("compras", 0)]:
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse("incidencia_lista")).context["n_accion"], n, usuario)

    def menu_incidencias(self, usuario):
        self.entrar(usuario)
        r = self.client.get(reverse("orden_lista") if usuario != "almacen" else reverse("recepcion_lista"))
        return [i for g in r.context["menu"] for i in g["items"] if i["etiqueta"] == "Incidencias"][0]

    def test_el_menu_cuenta_las_abiertas_para_todos(self):
        _, (inc,) = self.orden_con_incidencias()
        self.orden_con_incidencias()
        esperado = {"compras": True, "admin": False, "almacen": False}      # abierta → Compras
        for usuario in esperado:
            item = self.menu_incidencias(usuario)
            self.assertEqual(item["contador"], 2, usuario)                 # abiertas, no solo las mías
        self.proponer(inc, "Almacén")
        self.paso("admin", inc, "aceptar", texto="")
        self.completar(inc, "hecho")
        self.paso("admin", inc, "cerrar", texto="")                          # una se cierra → queda 1 abierta
        for usuario in esperado:
            self.assertEqual(self.menu_incidencias(usuario)["contador"], 1, usuario)

    def test_produccion_no_tiene_contador_de_incidencias(self):
        self.orden_con_incidencias()
        self.entrar("produccion")
        r = self.client.get(reverse("dashboard_produccion"))
        self.assertNotIn("Incidencias", [i["etiqueta"] for g in r.context["menu"] for i in g["items"]])


class ActualizacionesTests(BaseIncidencias):
    def fila(self, usuario, inc):
        self.entrar(usuario)
        r = self.client.get(reverse("incidencia_lista") + "?vista=todas")
        return next(i for i in r.context["incidencias"] if i.pk == inc.pk), r

    def test_un_mensaje_ajeno_marca_actualizaciones_hasta_que_lo_abres(self):
        _, (inc,) = self.orden_con_incidencias()
        # La apertura la crea Almacén: Compras y Admin no la han visto
        self.assertTrue(self.fila("compras", inc)[0].hay_actualizaciones)
        self.assertFalse(self.fila("almacen", inc)[0].hay_actualizaciones)      # es su propio mensaje
        self.entrar("compras")
        self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        self.assertFalse(self.fila("compras", inc)[0].hay_actualizaciones)
        self.assertTrue(self.fila("admin", inc)[0].hay_actualizaciones)         # cada usuario lleva lo suyo

    def test_publicar_en_el_hilo_avisa_a_los_demas_no_a_uno_mismo(self):
        _, (inc,) = self.orden_con_incidencias()
        for u in ("compras", "admin", "almacen"):
            self.entrar(u)
            self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        self.paso("compras", inc, "comentar", texto="¿Podéis fotografiarlo?")
        self.assertFalse(self.fila("compras", inc)[0].hay_actualizaciones)
        self.assertTrue(self.fila("almacen", inc)[0].hay_actualizaciones)
        self.assertTrue(self.fila("admin", inc)[0].hay_actualizaciones)
        r = self.client.get(reverse("incidencia_lista") + "?vista=todas")
        self.assertContains(r, "Novedad")

    def test_el_detalle_marca_los_mensajes_nuevos_solo_la_primera_vez(self):
        _, (inc,) = self.orden_con_incidencias()
        self.entrar("compras")
        self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        self.paso("almacen", inc, "comentar", texto="Adjunto foto")
        self.entrar("compras")
        r = self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        self.assertEqual(r.context["n_nuevos"], 1)
        self.assertContains(r, "tk-nuevo")
        r = self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        self.assertEqual(r.context["n_nuevos"], 0)

    def test_resuelta_o_vista_no_deja_aviso_y_las_consultas_son_constantes(self):
        for _ in range(2):
            self.orden_con_incidencias()
        self.entrar("admin")
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse("incidencia_lista") + "?vista=todas")
        for _ in range(4):
            self.orden_con_incidencias()
        self.entrar("admin")
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(reverse("incidencia_lista") + "?vista=todas")
        self.assertEqual(len(pocas), len(muchas))


class DetalleDisenoTests(BaseIncidencias):
    def test_el_detalle_muestra_proveedor_lineas_y_resalta_la_defectuosa(self):
        o, (inc,) = self.orden_con_incidencias(ng=(1,))
        self.entrar("compras")
        r = self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        prov = o.proveedor
        self.assertContains(r, f'href="mailto:{prov.email}"')
        self.assertContains(r, "Detalles de la incidencia")
        self.assertContains(r, "Resolución y tareas")
        self.assertContains(r, "Hilo de la incidencia")
        self.assertContains(r, "Línea con error", count=1)
        self.assertEqual(r.content.decode().count('class="is-fallo"'), 1)
        self.assertEqual(len(r.context["lineas_orden"]), o.lineas.count())

    def test_la_solucion_propuesta_aparece_tras_proponer(self):
        _, (inc,) = self.orden_con_incidencias()
        self.proponer(inc, "Almacén", "Reponer 5 unidades")
        self.entrar("admin")
        r = self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        self.assertContains(r, "Reponer 5 unidades")
        self.assertIn("Reponer 5 unidades", r.context["propuesta"].texto)

    def test_el_detalle_tiene_consultas_constantes(self):
        _, (a,) = self.orden_con_incidencias()
        self.entrar("admin")
        with CaptureQueriesContext(connection) as c1:
            self.client.get(reverse("incidencia_detalle", args=[a.pk]))
        for _ in range(5):
            self.paso("compras", a, "comentar", texto="hola")
        self.entrar("admin")
        with CaptureQueriesContext(connection) as c2:
            self.client.get(reverse("incidencia_detalle", args=[a.pk]))
        self.assertLessEqual(len(c2), len(c1) + 1)      # el get_or_create de la lectura puede variar en 1

    def test_la_lista_ordena_por_la_ultima_actualizacion(self):
        o1, (a,) = self.orden_con_incidencias()
        o2, (b,) = self.orden_con_incidencias()
        self.entrar("compras")
        orden = lambda **q: [i.pk for i in self.client.get(reverse("incidencia_lista"), q).context["incidencias"]]
        self.assertEqual(orden(), [b.pk, a.pk])                    # la más reciente arriba
        self.paso("almacen", a, "comentar", texto="Novedad en la primera")
        self.assertEqual(orden(), [a.pk, b.pk])                    # un mensaje la sube
        self.avanzar(b, "propuesta")                               # las tareas generadas también suben
        self.assertEqual(orden(), [b.pk, a.pk])
        self.assertEqual(orden(orden="apertura"), [a.pk, b.pk])    # más antiguas primero

    def test_filtros_de_antiguedad_categoria_y_texto(self):
        o1, (a,) = self.orden_con_incidencias()
        o2, (b,) = self.orden_con_incidencias()
        Incidencia.objects.filter(pk=a.pk).update(fecha_creacion=timezone.now() - timezone.timedelta(days=40))
        Incidencia.objects.filter(pk=b.pk).update(categoria="cantidad")
        self.entrar("compras")
        pk = lambda **q: {i.pk for i in self.client.get(reverse("incidencia_lista"), q).context["incidencias"]}
        self.assertEqual(pk(edad="mas30"), {a.pk})
        self.assertEqual(pk(edad="7"), {b.pk})
        self.assertEqual(pk(categoria="cantidad"), {b.pk})
        self.assertEqual(pk(q=o1.codigo), {a.pk})
        self.assertEqual(pk(q="zzz"), set())
        self.assertEqual(pk(edad="inventada", categoria="inventada"), {a.pk, b.pk})   # valores raros se ignoran

    def test_filtros_del_panel(self):
        o1, (a,) = self.orden_con_incidencias()
        o2, (b,) = self.orden_con_incidencias()
        self.avanzar(b, "solucionada"); self.paso("admin", b, "cerrar", texto="")
        self.entrar("compras")
        pk = lambda vista: {i.pk for i in self.client.get(reverse("incidencia_lista"), {"vista": vista}).context["incidencias"]}
        self.assertEqual(pk("accion"), {a.pk})
        self.assertEqual(pk("activas"), {a.pk})
        self.assertEqual(pk("resueltas"), {b.pk})
        self.assertEqual(pk("todas"), {a.pk, b.pk})
        self.assertEqual(pk("rarisimo"), {a.pk})                              # valor desconocido → activas

    def test_paneles_vacios(self):
        self.entrar("almacen")
        self.assertContains(self.client.get(reverse("incidencia_lista"), {"vista": "accion"}), "No tienes incidencias pendientes")

    def test_la_lista_no_hace_una_consulta_por_incidencia(self):
        self.orden_con_incidencias(ng=(0, 1))
        self.entrar("compras")
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse("incidencia_lista"))
        for _ in range(3):
            self.orden_con_incidencias(ng=(0, 1))
        self.entrar("compras")
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(reverse("incidencia_lista"))
        self.assertEqual(len(pocas), len(muchas))

    def test_el_detalle_ofrece_solo_las_acciones_de_cada_rol_y_fase(self):
        _, (inc,) = self.orden_con_incidencias()
        def acciones(usuario):
            self.entrar(usuario)
            return self.client.get(reverse("incidencia_detalle", args=[inc.pk])).context["acciones"]
        self.assertEqual(acciones("almacen"), ["comentar"])
        self.assertEqual(acciones("compras"), ["comentar", "proponer"])
        self.assertEqual(acciones("admin"), ["comentar"])
        self.proponer(inc, "Almacén")
        self.assertEqual(acciones("admin"), ["comentar", "aceptar", "rechazar"])
        self.assertEqual(acciones("compras"), ["comentar"])
        self.paso("admin", inc, "aceptar", texto="")
        self.assertEqual(acciones("almacen"), ["comentar"])                   # su tarea está en el menú Tareas
        self.assertEqual(acciones("admin"), ["comentar"])
        self.completar(inc, "hecho")
        self.assertEqual(acciones("admin"), ["comentar", "cerrar"])
        self.assertEqual(acciones("almacen"), ["comentar"])

    def test_el_detalle_pinta_el_hilo_y_el_aviso(self):
        _, (inc,) = self.orden_con_incidencias()
        self.paso("almacen", inc, "comentar", texto="Foto del palet dañado enviada por correo")
        self.entrar("compras")
        r = self.client.get(reverse("incidencia_detalle", args=[inc.pk]))
        self.assertContains(r, "Requiere tu acción")
        self.assertContains(r, "Foto del palet dañado")
        self.assertContains(r, "Problema 1")
        self.entrar("almacen")
        self.assertContains(self.client.get(reverse("incidencia_detalle", args=[inc.pk])), "Esperando la acción de")

    def test_el_historial_usa_frases_naturales_y_no_incluye_comentarios(self):
        o, (inc,) = self.orden_con_incidencias()
        self.paso("almacen", inc, "comentar", texto="comentario interno del hilo")
        self.avanzar(inc, "solucionada")
        self.paso("admin", inc, "cerrar", texto="")
        self.entrar("produccion")
        r = self.client.get(reverse("orden_detalle", args=[o.pk]))
        tipos = [(e["tipo"], e["m"].tipo if e["tipo"] == "incidencia" else e["h"].estado_nuevo) for e in r.context["actividad"]]
        self.assertEqual(tipos, [("estado", "en_camino"), ("estado", "incidencia"), ("incidencia", "apertura"),
                                 ("incidencia", "propuesta"), ("incidencia", "aceptacion"), ("incidencia", "tarea"),
                                 ("incidencia", "cierre"), ("estado", "recibido")])
        for frase in ("ha autorizado la orden y se ha enviado al proveedor", "ha verificado la recepción: hay material con incidencia",
                      "ha abierto una incidencia en", "ha propuesto una solución para", "ha aceptado la medida de",
                      "ha completado una tarea de la incidencia de", "ha cerrado la incidencia de", "la orden queda como recibida"):
            self.assertContains(r, frase)
        self.assertNotContains(r, reverse("incidencia_detalle", args=[inc.pk]))   # Producción no ve el ticket

    def test_produccion_ve_el_hilo_completo_con_soluciones_y_comentarios(self):
        o, (inc,) = self.orden_con_incidencias()
        self.paso("almacen", inc, "comentar", texto="comentario interno del hilo")
        self.proponer(inc, "Almacén", "Reponer en 48 horas")
        self.entrar("produccion")
        r = self.client.get(reverse("orden_detalle", args=[o.pk]))
        self.assertContains(r, "Hilo de la incidencia (3 mensajes)")
        self.assertContains(r, "comentario interno del hilo")
        self.assertContains(r, "Reponer en 48 horas")
        # solo lectura: sin formularios de acción
        self.assertNotContains(r, "incidencia_accion")
        self.assertNotContains(r, "comentar-texto")

    def test_los_demas_roles_no_duplican_el_hilo_en_la_orden(self):
        o, (inc,) = self.orden_con_incidencias()
        self.entrar("compras")
        self.assertNotContains(self.client.get(reverse("orden_detalle", args=[o.pk])), "Hilo de la incidencia (")

    def test_frases_de_creacion_y_recepcion_correcta(self):
        o = self.crear_orden("creada")
        self.entrar("produccion")
        self.assertEqual(self.client.get(reverse("orden_detalle", args=[o.pk])).status_code, 404)
        self.entrar("compras")
        self.assertContains(self.client.get(reverse("orden_detalle", args=[o.pk])), "ha creado la orden")


class MigracionDeDatosAntiguosTests(BaseIncidencias):
    def _antigua(self, **extra):
        o = self.crear_orden("incidencia")
        linea = o.lineas.first()
        return Incidencia.objects.create(linea=linea, descripcion="Rota", creado_por=self.usuarios["almacen"], **extra)

    def test_convierte_cada_caso_y_es_idempotente(self):
        abierta = self._antigua()
        gestionada = self._antigua(gestion_compras="Reposición", gestionada_por=self.usuarios["compras"], fecha_gestion=timezone.now())
        resuelta = self._antigua(gestion_compras="Abono", gestionada_por=self.usuarios["compras"], fecha_gestion=timezone.now(),
                                 resuelta=True, resuelta_por=self.usuarios["admin"], fecha_resolucion=timezone.now())
        orden_vieja = self.crear_orden("incidencia_resuelta")

        r = services.migrar_incidencias_antiguas()
        self.assertEqual(r, {"incidencias": 3, "ordenes": 1})
        for i in (abierta, gestionada, resuelta):
            i.refresh_from_db()
        self.assertEqual((abierta.fase, gestionada.fase, resuelta.fase), ("abierta", "propuesta", "resuelta"))
        self.assertEqual(gestionada.responsable, "Compras")
        self.assertEqual(list(abierta.mensajes.values_list("tipo", flat=True)), ["apertura"])
        self.assertEqual(list(gestionada.mensajes.values_list("tipo", flat=True)), ["apertura", "propuesta"])
        self.assertEqual(list(resuelta.mensajes.values_list("tipo", flat=True)), ["apertura", "propuesta", "cierre"])
        orden_vieja.refresh_from_db()
        self.assertEqual(orden_vieja.estado, "recibido")

        total = MensajeIncidencia.objects.count()
        self.assertEqual(services.migrar_incidencias_antiguas(), {"incidencias": 0, "ordenes": 0})
        self.assertEqual(MensajeIncidencia.objects.count(), total)

    def test_una_incidencia_migrada_se_puede_seguir_gestionando(self):
        inc = self._antigua(gestion_compras="Reposición", gestionada_por=self.usuarios["compras"], fecha_gestion=timezone.now())
        services.migrar_incidencias_antiguas()
        self.paso("admin", inc, "aceptar", texto="")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "aceptada")
        t = inc.tareas.get()                                     # la medida antigua pasa a ser una tarea de su responsable
        self.assertEqual((t.departamento, t.estado, t.texto), ("Compras", "pendiente", "Reposición"))
        self.completar(inc, "Reclamado al proveedor")
        inc.refresh_from_db()
        self.assertEqual(inc.fase, "solucionada")
