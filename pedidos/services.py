"""Lógica de negocio de las órdenes de compra (fuera de las vistas para poder probarla)."""
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
import re

from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format

from django.db.models import Exists, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from .context_processors import rol_actual
from .catalogo_incidencias import (ACCIONES_TAREA, ACCIONES_SISTEMA, CATEGORIAS_CON_UNIDADES, RESOLUCIONES,
                                    SUBTIPOS, etiqueta_accion, genera_reposicion, resoluciones_para)
from .models import (HistorialEstado, Incidencia, LecturaIncidencia, LineaPedido, MensajeIncidencia, Notificacion,
                     OrdenCompra, PlantillaEmail, Tarea, VisitaSeccion)


class TransicionInvalida(Exception):
    pass


@transaction.atomic
def crear_orden(*, usuario, form, formset):
    """Guarda la orden con sus líneas y deja la primera entrada del historial."""
    orden = form.save(commit=False)
    orden.creado_por = usuario
    orden.estado = "creada"
    orden.save()
    formset.instance = orden
    formset.save()
    HistorialEstado.objects.create(
        orden=orden, estado_anterior="", estado_nuevo="creada", usuario=usuario
    )
    return orden


def avisar_produccion(orden, tipo, incidencia=None):
    """Crea un aviso para cada operario de Producción, solo si la orden pidió avisar a Producción."""
    if not orden.notificar_produccion:
        return 0
    destinatarios = list(User.objects.filter(groups__name="Producción", is_active=True).distinct())
    Notificacion.objects.bulk_create([
        Notificacion(destinatario=u, tipo=tipo, orden=orden, incidencia=incidencia) for u in destinatarios
    ])
    return len(destinatarios)


@transaction.atomic
def autorizar_orden(orden, usuario):
    """Creada -> En camino, con traza en el historial."""
    orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
    if orden.estado != "creada":
        raise TransicionInvalida("Solo se pueden autorizar órdenes en estado «Creada».")
    anterior = orden.estado
    orden.estado = "en_camino"
    orden.save(update_fields=["estado"])
    HistorialEstado.objects.create(
        orden=orden, estado_anterior=anterior, estado_nuevo="en_camino", usuario=usuario
    )
    return orden


def _cantidad(valor):
    return format(valor.normalize(), "f")      # 10.00 -> "10", 2.50 -> "2.5"


VARIABLES_EMAIL = ("codigo", "proveedor")


def variables_desconocidas(texto):
    """Nombres entre llaves que no son variables válidas (para avisar al editar la plantilla)."""
    return sorted({n for n in re.findall(r"\{([^{}]*)\}", texto or "") if n not in VARIABLES_EMAIL})


def _rellenar(texto, valores):
    """Sustituye solo {codigo} y {proveedor}; cualquier otra cosa se deja tal cual (sin str.format, que es más permisivo)."""
    return re.sub(r"\{(codigo|proveedor)\}", lambda m: valores[m.group(1)], texto or "")


def datos_orden(orden):
    """Lo que necesita el email de una orden real."""
    firmante = orden.creado_por
    return {
        "codigo": orden.codigo,
        "proveedor": orden.proveedor.nombre,
        "lineas": [
            {"articulo": l.producto.nombre, "cantidad": _cantidad(l.cantidad_pedida), "unidad": l.unidad_medida.nombre}
            for l in orden.lineas.select_related("producto", "unidad_medida").order_by("pk")
        ],
        "firmante": firmante.get_full_name() or firmante.get_username(),
    }


DATOS_EJEMPLO = {
    "codigo": "OC-2026-0001", "proveedor": "Proveedor de ejemplo S.L.", "firmante": "Nombre del operario de Compras",
    "lineas": [
        {"articulo": "Etanol 96%", "cantidad": "10", "unidad": "Cajas"},
        {"articulo": "Sosa cáustica", "cantidad": "2,5", "unidad": "Kg"},
    ],
}


def componer_email(datos, plantilla=None):
    """Devuelve (asunto, texto, html) con la estructura fija: saludo, introducción, tabla, cierre, firma y fecha."""
    plantilla = plantilla or PlantillaEmail.obtener()
    valores = {"codigo": datos["codigo"], "proveedor": datos["proveedor"]}
    contexto = {
        "saludo": _rellenar(plantilla.saludo, valores),
        "introduccion": _rellenar(plantilla.introduccion, valores),
        "cierre": _rellenar(plantilla.cierre, valores),
        "firma": _rellenar(plantilla.firma, valores),
        "codigo": datos["codigo"],
        "lineas": datos["lineas"],
        "firmante": datos["firmante"],
        "fecha": date_format(timezone.localdate(), "j \\d\\e F \\d\\e Y"),
    }
    asunto = " ".join(_rellenar(plantilla.asunto, valores).split())          # una sola línea, sin saltos
    return asunto, render_to_string("pedidos/email/orden.txt", contexto).strip() + "\n", render_to_string("pedidos/email/orden.html", contexto)


def enviar_email_orden(orden):
    """Envía la orden autorizada al proveedor con la plantilla configurada (HTML + texto plano)."""
    asunto, texto, html = componer_email(datos_orden(orden))
    mensaje = EmailMultiAlternatives(subject=asunto, body=texto, from_email=None, to=[orden.proveedor.email])
    mensaje.attach_alternative(html, "text/html")
    mensaje.send(fail_silently=False)


# ---------------------------------------------------------------------------
# Recepción en Almacén (F3-04) e incidencias automáticas (F3-05)
# ---------------------------------------------------------------------------
ESTADOS_RECIBIBLES = ("en_camino",)


def lineas_a_verificar(orden):
    """Todas las líneas de la orden: Almacén marca OK o NG en cada una."""
    return list(orden.lineas.select_related("producto", "unidad_medida").order_by("pk"))


@transaction.atomic
def registrar_recepcion(orden, usuario, resultados):
    """Guarda el OK/NG de cada línea y mueve la orden de estado.

    `resultados` = {pk_linea: {"resultado": "ok"|"ng", "descripcion": str, "cantidad": Decimal|None}}
    - Todo OK  -> la orden pasa a «recibido».
    - Alguna NG -> se crea una Incidencia por cada línea NG y la orden pasa a «incidencia».
    Devuelve (orden, número_de_incidencias_creadas).
    """
    orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
    if orden.estado not in ESTADOS_RECIBIBLES:
        raise TransicionInvalida("Solo se puede verificar la recepción de órdenes «En camino».")

    lineas = lineas_a_verificar(orden)
    if {l.pk for l in lineas} != set(resultados):
        raise TransicionInvalida("Hay que marcar OK o NG en todas las líneas pendientes.")

    incidencias = 0
    for linea in lineas:
        dato = resultados[linea.pk]
        if dato["resultado"] == "ok":
            linea.resultado_verificacion = "ok"
            linea.cantidad_verificada = linea.cantidad_pedida
        elif dato["resultado"] == "ng":
            categoria, subtipo, recibida, afectadas, texto = _validar_ng(linea, dato)
            linea.resultado_verificacion = "ng"
            linea.cantidad_verificada = recibida
            descripcion = describir_incidencia(linea, categoria, subtipo, afectadas, texto)
            incidencia = Incidencia.objects.create(
                linea=linea, descripcion=descripcion, creado_por=usuario,
                categoria=categoria, subtipo=subtipo, unidades_afectadas=afectadas,
            )
            _mensaje(incidencia, usuario, "apertura", descripcion)
            incidencias += 1
        else:
            raise ValueError("Resultado de verificación desconocido.")
        linea.save(update_fields=["resultado_verificacion", "cantidad_verificada"])

    anterior = orden.estado
    orden.estado = "incidencia" if incidencias else "recibido"
    orden.save(update_fields=["estado"])
    HistorialEstado.objects.create(
        orden=orden, estado_anterior=anterior, estado_nuevo=orden.estado, usuario=usuario
    )
    avisar_produccion(orden, "con_incidencia" if incidencias else "recibida")
    _completar_tareas_reposicion(orden, usuario)
    return orden, incidencias


def _n(valor):
    """Decimal sin ceros sobrantes: 100.00 -> «100»."""
    texto = f"{valor:f}"
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


def _validar_ng(linea, dato):
    """Comprueba los datos de una línea NG según su categoría. Devuelve (categoria, subtipo, recibida, afectadas, texto)."""
    categoria = dato.get("categoria") or ""
    if categoria not in dict(Incidencia.CATEGORIAS):
        raise ValueError("Elige la categoría de la incidencia.")
    texto = (dato.get("descripcion") or "").strip()
    subtipo = dato.get("subtipo") or ""
    recibida = dato.get("cantidad")
    afectadas = dato.get("afectadas")
    if categoria == "cantidad":
        if recibida is None:
            raise ValueError("Indica cuántas unidades se han recibido.")
        if recibida == linea.cantidad_pedida:
            raise ValueError("La cantidad recibida coincide con la pedida: no hay error de cantidad.")
        subtipo = "faltan" if recibida < linea.cantidad_pedida else "sobran"
        afectadas = abs(linea.cantidad_pedida - recibida)
        return categoria, subtipo, recibida, afectadas, texto
    if categoria in SUBTIPOS and subtipo not in dict(SUBTIPOS[categoria]):
        raise ValueError("Elige el tipo de problema.")
    if categoria in CATEGORIAS_CON_UNIDADES:
        if not afectadas or afectadas <= 0:
            raise ValueError("Indica cuántas unidades están afectadas.")
    else:
        afectadas = None
    if categoria == "otra" and not texto:
        raise ValueError("Describe el problema.")
    return categoria, subtipo, None, afectadas, texto


def describir_incidencia(linea, categoria, subtipo, afectadas, texto):
    """Texto legible de la incidencia a partir de lo que registró Almacén."""
    unidad = linea.unidad_medida.nombre
    etiqueta = dict(Incidencia.CATEGORIAS)[categoria]
    if categoria == "cantidad":
        sentido = "Faltan" if subtipo == "faltan" else "Sobran"
        recibida = linea.cantidad_pedida - afectadas if subtipo == "faltan" else linea.cantidad_pedida + afectadas
        base = f"{etiqueta}: {sentido.lower()} {_n(afectadas)} {unidad} (pedidas {_n(linea.cantidad_pedida)}, recibidas {_n(recibida)})."
    else:
        partes = [etiqueta]
        if subtipo:
            partes.append(dict(SUBTIPOS[categoria]).get(subtipo, subtipo))
        base = ": ".join(partes)
        if afectadas:
            base += f" · {_n(afectadas)} {unidad} afectadas"
        base += "."
    return f"{base}\n{texto}" if texto else base




# ---------------------------------------------------------------------------
# Incidencias como ticket con tareas
#
#   abierta ─(Compras propone resolución + tareas)─▶ propuesta ─(Admin aprueba)─▶ aceptada («Tareas abiertas»)
#      ▲                                                │                              │ cada departamento completa lo suyo
#      └────────────(Admin devuelve)───────────────────┘                              ▼
#                          reposicion («Esperando reposición») ◀── solo queda recibir la orden de reposición
#                                                                                       │
#                                                    solucionada («Gestionada») ◀───────┘
#                                                       │ (Admin verifica y cierra, o reabre una tarea)
#                                                       ▼
#                                                    resuelta («Cerrada»)  y la orden pasa a «recibido»
#
# Si la medida aprobada no necesita tareas (p. ej. «Aceptar el material»), se cierra al aprobarla.
# ---------------------------------------------------------------------------
ROLES_TICKET = ("Administrador", "Compras", "Almacén")


class SinPermiso(Exception):
    """El rol del usuario no puede hacer esta acción (la vista lo convierte en un 403)."""


def _pendientes_manuales(incidencia):
    return [t for t in incidencia.tareas.all() if t.estado == "pendiente" and not t.automatica]


def rol_que_debe_actuar(incidencia):
    """A quién le toca mover el ticket ahora (None si está cerrada). Con varias tareas: «Almacén y Compras»."""
    fase = incidencia.fase
    if fase == "abierta":
        return "Compras"
    if fase in ("propuesta", "solucionada"):
        return "Administrador"
    if fase == "reposicion":
        return "Almacén"
    if fase == "aceptada":
        departamentos = sorted({t.departamento for t in _pendientes_manuales(incidencia)})
        return " y ".join(departamentos) or incidencia.responsable or "Compras"
    return None


def q_requiere_accion(rol):
    """Filtro de las incidencias que esperan una acción de este rol."""
    pendientes = Tarea.objects.filter(incidencia=OuterRef("pk"), estado="pendiente", automatica=False, departamento=rol)
    consulta = Q(fase="aceptada") & Exists(pendientes)
    if rol == "Compras":
        consulta |= Q(fase="abierta")
    if rol == "Administrador":
        consulta |= Q(fase__in=("propuesta", "solucionada"))
    if rol == "Almacén":
        consulta |= Q(fase="reposicion")
    return consulta


def contar_incidencias_abiertas():
    return Incidencia.objects.filter(resuelta=False).count()


def contar_tareas_pendientes(rol):
    return Tarea.objects.filter(estado="pendiente", departamento=rol).count()


def incidencias_con_actualizaciones(usuario):
    """Anotación Exists: True si el hilo tiene mensajes de otras personas posteriores a lo último que vio el usuario."""
    nuevos = (MensajeIncidencia.objects.filter(incidencia=OuterRef("pk"))
              .exclude(autor=usuario)
              .filter(pk__gt=Coalesce(Subquery(LecturaIncidencia.objects.filter(usuario=usuario, incidencia=OuterRef(OuterRef("pk")))
                                                .values("ultimo_mensaje")[:1]), Value(0))))
    return Exists(nuevos)


def marcar_incidencia_vista(incidencia, usuario):
    """Registra que el usuario ha visto todo el hilo. Devuelve el mensaje hasta el que había visto antes."""
    ultimo = incidencia.mensajes.order_by("-pk").values_list("pk", flat=True).first() or 0
    lectura, _ = LecturaIncidencia.objects.get_or_create(usuario=usuario, incidencia=incidencia)
    anterior = lectura.ultimo_mensaje
    if ultimo > anterior:
        lectura.ultimo_mensaje = ultimo
        lectura.save(update_fields=["ultimo_mensaje"])
    return anterior


def marcar_visita(usuario, seccion):
    """Registra que el usuario ha abierto una sección. Devuelve la visita anterior (None si es la primera)."""
    ahora = timezone.now()
    visita, creada = VisitaSeccion.objects.get_or_create(usuario=usuario, seccion=seccion, defaults={"ultima_visita": ahora})
    if creada:
        return None
    anterior = visita.ultima_visita
    visita.ultima_visita = ahora
    visita.save(update_fields=["ultima_visita"])
    return anterior


def novedades_menu(usuario, rol):
    """{nombre de URL: True} para las secciones con algo nuevo desde la última visita (el contador no lo detecta:
    si una tarea se cierra y otra se abre, el número no cambia)."""
    visitas = dict(VisitaSeccion.objects.filter(usuario=usuario).values_list("seccion", "ultima_visita"))
    base = usuario.date_joined
    novedades = {}
    if rol in ROLES_TICKET:
        novedades["incidencia_lista"] = (MensajeIncidencia.objects.filter(fecha__gt=visitas.get("incidencias", base))
                                         .exclude(autor=usuario).exists())
    novedades["tarea_lista"] = Tarea.objects.filter(
        estado="pendiente", departamento=rol, visible_desde__gt=visitas.get("tareas", base)).exists()
    return novedades


def contar_notificaciones_sin_leer(usuario):
    return Notificacion.objects.filter(destinatario=usuario, leida=False).count()


def contar_ordenes_por_autorizar():
    return OrdenCompra.objects.filter(estado="creada").count()


def contar_acciones_pendientes(rol):
    if rol not in ROLES_TICKET:
        return 0
    return Incidencia.objects.filter(q_requiere_accion(rol)).count()


def _mensaje(incidencia, usuario, tipo, texto=""):
    return MensajeIncidencia.objects.create(
        incidencia=incidencia, autor=usuario, rol=rol_actual(usuario) or "", tipo=tipo, texto=(texto or "").strip()
    )


def _bloquear(incidencia):
    """Bloquea primero la orden y luego la incidencia (siempre en este orden, para evitar bloqueos cruzados)."""
    orden_pk = Incidencia.objects.values_list("linea__orden_id", flat=True).get(pk=incidencia.pk)
    orden = OrdenCompra.objects.select_for_update().get(pk=orden_pk)
    incidencia = Incidencia.objects.select_for_update().get(pk=incidencia.pk)
    return orden, incidencia


def _exigir_rol(usuario, *roles):
    if rol_actual(usuario) not in roles:
        raise SinPermiso("Tu rol no puede realizar esta acción.")


def _exigir_fase(incidencia, *fases):
    if incidencia.fase not in fases:
        raise TransicionInvalida("Esta acción ya no está disponible: la incidencia ha cambiado de fase.")


def _texto_obligatorio(texto, aviso):
    texto = (texto or "").strip()
    if not texto:
        raise ValueError(aviso)
    return texto


@transaction.atomic
def comentar_incidencia(incidencia, usuario, texto):
    """Cualquier rol del ticket aporta pruebas o información mientras no esté cerrada."""
    _exigir_rol(usuario, *ROLES_TICKET)
    texto = _texto_obligatorio(texto, "Escribe un mensaje.")
    _, incidencia = _bloquear(incidencia)
    if incidencia.fase == "resuelta":
        raise TransicionInvalida("La incidencia está cerrada y ya no admite mensajes.")
    return _mensaje(incidencia, usuario, "comentario", texto)


def _validar_tareas(tareas):
    """Cada tarea = {departamento, accion, texto}. Devuelve la lista limpia."""
    limpias = []
    for t in tareas:
        accion, departamento, texto = t.get("accion"), t.get("departamento"), (t.get("texto") or "").strip()
        if accion not in ACCIONES_TAREA or accion in ACCIONES_SISTEMA or departamento not in ACCIONES_TAREA[accion][1]:
            raise ValueError("Esa acción no la puede realizar el departamento elegido.")
        if accion == "otra" and not texto:
            raise ValueError("Describe la acción en el texto libre.")
        limpias.append({"departamento": departamento, "accion": accion, "texto": texto})
    return limpias


@transaction.atomic
def proponer_solucion(incidencia, usuario, resolucion, tareas, unidades_reposicion=None):
    """Compras elige la resolución y define las tareas. Quedan en borrador hasta que el Administrador apruebe."""
    _exigir_rol(usuario, "Compras")
    _, incidencia = _bloquear(incidencia)
    _exigir_fase(incidencia, "abierta")
    if resolucion not in resoluciones_para(incidencia.categoria, incidencia.subtipo):
        raise ValueError("Elige una resolución válida para esta categoría.")
    tareas = _validar_tareas(tareas)
    repone = genera_reposicion(resolucion)
    if not tareas and not repone and resolucion != "aceptar":
        raise ValueError("Añade al menos una tarea para llevar a cabo la resolución.")
    if repone:
        if unidades_reposicion is None:
            unidades_reposicion = incidencia.unidades_afectadas or incidencia.linea.cantidad_pedida
        if unidades_reposicion <= 0:
            raise ValueError("Indica cuántas unidades hay que reponer.")
    else:
        unidades_reposicion = None

    incidencia.tareas.filter(estado="borrador").delete()
    for t in tareas:
        Tarea.objects.create(incidencia=incidencia, creada_por=usuario, estado="borrador", **t)
    etiqueta = RESOLUCIONES[resolucion][0]
    lineas = [f"Resolución: {etiqueta}."]
    if repone:
        lineas.append(f"Se repondrán {_n(unidades_reposicion)} {incidencia.linea.unidad_medida.nombre} con una orden de tipo «incidencia» (sin email al proveedor).")
    for t in tareas:
        extra = f" — {t['texto']}" if t["texto"] else ""
        lineas.append(f"• {t['departamento']}: {etiqueta_accion(t['accion'])}{extra}")
    resumen = "\n".join(lineas)
    incidencia.fase = "propuesta"
    incidencia.resolucion = resolucion
    incidencia.unidades_reposicion = unidades_reposicion
    incidencia.gestion_compras = resumen
    incidencia.gestionada_por = usuario
    incidencia.fecha_gestion = timezone.now()
    incidencia.save(update_fields=["fase", "resolucion", "unidades_reposicion", "gestion_compras",
                                   "gestionada_por", "fecha_gestion"])
    _mensaje(incidencia, usuario, "propuesta", resumen)
    return incidencia


def crear_orden_reposicion(incidencia, usuario):
    """Orden de tipo «incidencia»: nace «En camino», enlazada a la original y SIN email al proveedor."""
    linea = incidencia.linea
    unidades = incidencia.unidades_reposicion or incidencia.unidades_afectadas or linea.cantidad_pedida
    orden = OrdenCompra.objects.create(creado_por=usuario, estado="en_camino", tipo="incidencia", origen=incidencia)
    LineaPedido.objects.create(orden=orden, producto=linea.producto, unidad_medida=linea.unidad_medida,
                               cantidad_pedida=unidades)
    HistorialEstado.objects.create(orden=orden, estado_anterior="", estado_nuevo="en_camino", usuario=usuario)
    return orden


def _avanzar(incidencia):
    """Recalcula la fase según las tareas pendientes (no cambia nada si aún no se han aprobado)."""
    pendientes = list(incidencia.tareas.filter(estado="pendiente"))
    if any(not t.automatica for t in pendientes):
        fase = "aceptada"
    elif pendientes:
        fase = "reposicion"
    else:
        fase = "solucionada"
    if fase != incidencia.fase:
        incidencia.fase = fase
        incidencia.save(update_fields=["fase"])
    return fase


def _cerrar(orden, incidencia, usuario, texto=""):
    """Cierra la incidencia. Si era la última abierta de la orden, la orden pasa a «recibido». Devuelve orden_cerrada."""
    incidencia.fase = "resuelta"
    incidencia.resuelta = True
    incidencia.resuelta_por = usuario
    incidencia.fecha_resolucion = timezone.now()
    incidencia.save(update_fields=["fase", "resuelta", "resuelta_por", "fecha_resolucion"])
    _mensaje(incidencia, usuario, "cierre", texto)

    quedan_abiertas = Incidencia.objects.filter(linea__orden=orden, resuelta=False).exists()
    orden_cerrada = False
    if orden.estado == "incidencia" and not quedan_abiertas:
        orden.estado = "recibido"
        orden.save(update_fields=["estado"])
        HistorialEstado.objects.create(
            orden=orden, estado_anterior="incidencia", estado_nuevo="recibido", usuario=usuario
        )
        orden_cerrada = True
    avisar_produccion(orden, "incidencia_resuelta", incidencia)
    return orden_cerrada


@transaction.atomic
def aceptar_propuesta(incidencia, usuario, texto=""):
    """El Administrador aprueba la medida: las tareas pasan a los departamentos y, si procede, nace la orden de reposición.
    Si no hay ninguna tarea que hacer, la incidencia se cierra directamente."""
    _exigir_rol(usuario, "Administrador")
    orden, incidencia = _bloquear(incidencia)
    _exigir_fase(incidencia, "propuesta")
    ahora = timezone.now()
    borradores = incidencia.tareas.filter(estado="borrador")
    if not borradores.exists() and not incidencia.resolucion and incidencia.gestion_compras.strip():
        # incidencia anterior al sistema de tareas: la medida pasa a ser una tarea de su responsable
        Tarea.objects.create(incidencia=incidencia, creada_por=incidencia.gestionada_por or usuario, estado="borrador",
                             departamento=incidencia.responsable or "Compras", accion="otra", texto=incidencia.gestion_compras)
        borradores = incidencia.tareas.filter(estado="borrador")
    borradores.update(estado="pendiente", visible_desde=ahora)

    aviso = ""
    if genera_reposicion(incidencia.resolucion):
        reposicion = crear_orden_reposicion(incidencia, usuario)
        Tarea.objects.create(
            incidencia=incidencia, creada_por=usuario, estado="pendiente", automatica=True, departamento="Almacén",
            accion="recepcionar_reposicion", orden_reposicion=reposicion, visible_desde=ahora,
            texto=f"Recepcionar la orden {reposicion.codigo} cuando llegue.")
        aviso = f"\nSe ha creado la orden de reposición {reposicion.codigo}."
    _mensaje(incidencia, usuario, "aceptacion", f"{(texto or '').strip()}{aviso}".strip())

    if not incidencia.tareas.exists():
        _cerrar(orden, incidencia, usuario, "Medida aceptada: no requiere tareas.")
        return incidencia
    _avanzar(incidencia)
    return incidencia


@transaction.atomic
def rechazar_propuesta(incidencia, usuario, motivo):
    """El Administrador no aprueba la medida: vuelve a Compras con el motivo y se descartan las tareas en borrador."""
    _exigir_rol(usuario, "Administrador")
    motivo = _texto_obligatorio(motivo, "Indica el motivo del rechazo.")
    _, incidencia = _bloquear(incidencia)
    _exigir_fase(incidencia, "propuesta")
    incidencia.tareas.filter(estado="borrador").delete()
    incidencia.fase = "abierta"
    incidencia.save(update_fields=["fase"])
    _mensaje(incidencia, usuario, "rechazo", motivo)
    return incidencia


@transaction.atomic
def completar_tarea(tarea, usuario, texto=""):
    """El departamento asignado marca su tarea como hecha. Las tareas del sistema se completan solas."""
    tarea = Tarea.objects.get(pk=tarea.pk)
    _, incidencia = _bloquear(tarea.incidencia)
    tarea = Tarea.objects.select_for_update().get(pk=tarea.pk)
    if tarea.automatica:
        raise TransicionInvalida("Esta tarea se completa sola cuando Almacén recibe la mercancía.")
    if tarea.estado != "pendiente":
        raise TransicionInvalida("La tarea ya no está pendiente.")
    if rol_actual(usuario) != tarea.departamento:
        raise SinPermiso("Esta tarea es de otro departamento.")
    tarea.estado = "completada"
    tarea.completada_por = usuario
    tarea.fecha_completada = timezone.now()
    tarea.save(update_fields=["estado", "completada_por", "fecha_completada"])
    nota = (texto or "").strip()
    _mensaje(incidencia, usuario, "tarea", f"Tarea completada: {etiqueta_accion(tarea.accion)}.{(' ' + nota) if nota else ''}")
    _avanzar(incidencia)
    return tarea


def _completar_tareas_reposicion(orden, usuario):
    """Al verificar una orden de reposición, se completa sola la tarea «Recepcionar la reposición»."""
    if orden.tipo != "incidencia":
        return
    for tarea in Tarea.objects.filter(orden_reposicion=orden, estado="pendiente", automatica=True):
        _, incidencia = _bloquear(tarea.incidencia)
        tarea.estado = "completada"
        tarea.completada_por = usuario
        tarea.fecha_completada = timezone.now()
        tarea.save(update_fields=["estado", "completada_por", "fecha_completada"])
        _mensaje(incidencia, usuario, "tarea", f"Reposición recibida: la orden {orden.codigo} ya está en el almacén.")
        _avanzar(incidencia)


@transaction.atomic
def reabrir_tarea(tarea, usuario, motivo):
    """El Administrador considera insuficiente una tarea: vuelve a su departamento con el motivo."""
    _exigir_rol(usuario, "Administrador")
    motivo = _texto_obligatorio(motivo, "Indica qué falta por hacer.")
    tarea = Tarea.objects.get(pk=tarea.pk)
    _, incidencia = _bloquear(tarea.incidencia)
    tarea = Tarea.objects.select_for_update().get(pk=tarea.pk)
    _exigir_fase(incidencia, "aceptada", "reposicion", "solucionada")
    if tarea.automatica or tarea.estado != "completada":
        raise TransicionInvalida("Solo se pueden reabrir tareas completadas por una persona.")
    tarea.estado = "pendiente"
    tarea.visible_desde = timezone.now()
    tarea.completada_por = None
    tarea.fecha_completada = None
    tarea.save(update_fields=["estado", "visible_desde", "completada_por", "fecha_completada"])
    _mensaje(incidencia, usuario, "devolucion", f"Tarea reabierta ({etiqueta_accion(tarea.accion)}): {motivo}")
    _avanzar(incidencia)
    return tarea


@transaction.atomic
def cerrar_incidencia(incidencia, usuario, texto=""):
    """El Administrador verifica la gestión y cierra la incidencia (todas las tareas hechas y la reposición recibida).

    Si era la última abierta de la orden, la orden pasa a «recibido».
    Devuelve (incidencia, orden_cerrada: bool).
    """
    _exigir_rol(usuario, "Administrador")
    orden, incidencia = _bloquear(incidencia)
    _exigir_fase(incidencia, "solucionada")
    orden_cerrada = _cerrar(orden, incidencia, usuario, texto)
    return incidencia, orden_cerrada


def rol_puede(accion, incidencia, usuario):
    """¿Puede este rol hacer esta acción en algún momento del ticket? (independiente de la fase)"""
    rol = rol_actual(usuario)
    if accion == "comentar":
        return rol in ROLES_TICKET
    if accion == "proponer":
        return rol == "Compras"
    if accion in ("aceptar", "rechazar", "cerrar"):
        return rol == "Administrador"
    return False


def acciones_disponibles(incidencia, usuario):
    """Acciones que este usuario puede hacer ahora en el ticket (para pintar los formularios)."""
    rol = rol_actual(usuario)
    if rol not in ROLES_TICKET or incidencia.fase == "resuelta":
        return []
    acciones = ["comentar"]
    if incidencia.fase == "abierta" and rol == "Compras":
        acciones.append("proponer")
    if incidencia.fase == "propuesta" and rol == "Administrador":
        acciones += ["aceptar", "rechazar"]
    if incidencia.fase == "solucionada" and rol == "Administrador":
        acciones.append("cerrar")
    return acciones


def migrar_incidencias_antiguas():
    """Convierte lo creado antes del ticket (una sola gestión) al formato actual. Es idempotente.

    - Cada incidencia sin hilo recibe su mensaje de apertura y, si procede, de propuesta y cierre.
    - Las órdenes que estaban en «incidencia resuelta» pasan a «recibido» (ya no hay re-verificación).
    - Las incidencias «en ejecución» sin tareas reciben una tarea pendiente para su responsable.
    """
    convertidas = 0
    for inc in Incidencia.objects.filter(mensajes__isnull=True).select_related("creado_por", "gestionada_por", "resuelta_por"):
        def poner(autor, tipo, texto, fecha):
            m = MensajeIncidencia.objects.create(incidencia=inc, autor=autor, rol="", tipo=tipo, texto=texto)
            # nunca antes de la apertura, para que el hilo quede en orden cronológico
            MensajeIncidencia.objects.filter(pk=m.pk).update(fecha=max(fecha, inc.fecha_creacion) if fecha else inc.fecha_creacion)
        poner(inc.creado_por, "apertura", inc.descripcion, inc.fecha_creacion)
        if inc.resuelta:
            inc.fase = "resuelta"
            if inc.gestion_compras.strip() and inc.gestionada_por:
                poner(inc.gestionada_por, "propuesta", inc.gestion_compras, inc.fecha_gestion)
            if inc.resuelta_por:
                poner(inc.resuelta_por, "cierre", "", inc.fecha_resolucion)
        elif inc.gestion_compras.strip():
            inc.fase = "propuesta"
            inc.responsable = inc.responsable or "Compras"
            if inc.gestionada_por:
                poner(inc.gestionada_por, "propuesta", inc.gestion_compras, inc.fecha_gestion)
        inc.save(update_fields=["fase", "responsable"])
        convertidas += 1

    for inc in Incidencia.objects.filter(fase="aceptada", tareas__isnull=True):
        Tarea.objects.create(incidencia=inc, creada_por=inc.gestionada_por or inc.creado_por, estado="pendiente",
                             departamento=inc.responsable or "Compras", accion="otra", texto=inc.gestion_compras,
                             visible_desde=timezone.now())

    ordenes = 0
    for orden in OrdenCompra.objects.filter(estado="incidencia_resuelta"):
        ultimo = orden.historial.order_by("-fecha").first()
        orden.estado = "recibido"
        orden.save(update_fields=["estado"])
        if ultimo:
            HistorialEstado.objects.create(
                orden=orden, estado_anterior="incidencia_resuelta", estado_nuevo="recibido", usuario=ultimo.usuario
            )
        ordenes += 1
    return {"incidencias": convertidas, "ordenes": ordenes}
