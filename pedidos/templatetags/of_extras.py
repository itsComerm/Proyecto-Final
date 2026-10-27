from django import template
from django.utils.html import format_html

register = template.Library()

# clave del modelo -> (tono del badge, etiqueta, icono)
ESTADOS = {
    "creada": ("draft", "Creada", "doc"),
    "en_camino": ("transit", "En camino", "truck"),
    "incidencia": ("incident", "Con incidencia", "alert"),
    "incidencia_resuelta": ("approved", "Incidencia resuelta", "check"),
    "recibido": ("received", "Recibida", "check"),
}


@register.simple_tag
def estado_badge(clave):
    """Insignia de estado: siempre icono + palabra (el color nunca va solo)."""
    tono, etiqueta, icono = ESTADOS.get(clave, ("draft", clave, "doc"))
    return format_html(
        '<span class="of-badge of-badge-{}"><svg class="of-ico"><use href="#i-{}"/></svg>{}</span>',
        tono, icono, etiqueta,
    )


@register.filter
def estado_etiqueta(clave):
    """'en_camino' -> 'En camino' (para textos, sin insignia)."""
    return ESTADOS.get(clave, ("", clave, ""))[1]


# fase de la incidencia -> (tono del badge, etiqueta, icono)
FASES_INCIDENCIA = {
    "abierta": ("incident", "Abierta", "alert"),
    "propuesta": ("approved", "Solución propuesta", "doc"),
    "aceptada": ("transit", "Tareas abiertas", "truck"),
    "reposicion": ("transit", "Esperando reposición", "truck"),
    "solucionada": ("approved", "Gestionada", "check"),
    "resuelta": ("received", "Cerrada", "check"),
}

# tipo de mensaje del hilo -> (tono, icono)
TIPOS_MENSAJE = {
    "apertura": ("incident", "alert"),
    "propuesta": ("approved", "doc"),
    "aceptacion": ("received", "check"),
    "rechazo": ("incident", "alert"),
    "ejecucion": ("transit", "truck"),
    "devolucion": ("incident", "alert"),
    "cierre": ("received", "check"),
    "comentario": ("draft", "doc"),
    "tarea": ("transit", "check"),
}


@register.simple_tag
def incidencia_badge(incidencia):
    """Insignia de la fase del ticket."""
    tono, etiqueta, icono = FASES_INCIDENCIA[incidencia.fase]
    return format_html(
        '<span class="of-badge of-badge-{}"><svg class="of-ico"><use href="#i-{}"/></svg>{}</span>',
        tono, icono, etiqueta,
    )


@register.simple_tag
def mensaje_badge(mensaje):
    """Insignia del tipo de evento del hilo."""
    tono, icono = TIPOS_MENSAJE.get(mensaje.tipo, ("draft", "doc"))
    return format_html(
        '<span class="of-badge of-badge-{}"><svg class="of-ico"><use href="#i-{}"/></svg>{}</span>',
        tono, icono, mensaje.get_tipo_display(),
    )


@register.filter
def iniciales(usuario):
    nombre = usuario.get_full_name() or usuario.get_username()
    return "".join(p[0] for p in nombre.split()[:2]).upper() or "U"


def _nombre(usuario):
    return usuario.get_full_name() or usuario.get_username()


@register.simple_tag
def frase_estado(h):
    """Frase natural para un cambio de estado de la orden: «Ana ha creado la orden»."""
    n = h.estado_nuevo
    if n == "creada":
        accion = "ha creado la orden"
    elif n == "en_camino":
        accion = "ha autorizado la orden y se ha enviado al proveedor"
    elif n == "incidencia":
        accion = "ha verificado la recepción: hay material con incidencia"
    elif n == "recibido" and h.estado_anterior == "en_camino":
        accion = "ha verificado la recepción: todo correcto"
    elif n == "incidencia_resuelta":
        accion = "ha resuelto la incidencia"
    elif n == "recibido":
        accion = "ha cerrado la última incidencia: la orden queda como recibida"
    else:
        accion = f"ha cambiado la orden a «{ESTADOS.get(n, ('', n, ''))[1]}»"
    return format_html("<b>{}</b> {}", _nombre(h.usuario), accion)


FRASES_MENSAJE = {
    "apertura": "ha abierto una incidencia en",
    "comentario": "ha comentado en la incidencia de",
    "propuesta": "ha propuesto una solución para",
    "aceptacion": "ha aceptado la medida de",
    "rechazo": "ha rechazado la solución propuesta para",
    "ejecucion": "ha gestionado la incidencia de",
    "devolucion": "ha devuelto a ejecución la incidencia de",
    "cierre": "ha cerrado la incidencia de",
    "tarea": "ha completado una tarea de la incidencia de",
}


@register.simple_tag
def frase_mensaje(mensaje, producto):
    """Frase natural para un hito del ticket: «Ana ha propuesto una solución para «Etanol»»."""
    return format_html("<b>{}</b> {} «{}»", _nombre(mensaje.autor),
                       FRASES_MENSAJE.get(mensaje.tipo, "ha actuado en la incidencia de"), producto)


@register.filter
def dict_get(diccionario, clave):
    """{{ dic|dict_get:clave }}"""
    return (diccionario or {}).get(clave, "")
