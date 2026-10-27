import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from .decorators import rol_requerido
from .models import HistorialEstado, MensajeIncidencia

POR_PAGINA = 30
TIPOS = {"": "Todo", "ordenes": "Órdenes", "incidencias": "Incidencias"}
RANGOS = {"hoy": "Hoy", "7": "Últimos 7 días", "30": "Últimos 30 días", "todo": "Todo el historial"}
RANGO_POR_DEFECTO = "7"


def _desde(rango):
    ahora = timezone.localtime()
    if rango == "hoy":
        return ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    if rango in ("7", "30"):
        return ahora - timedelta(days=int(rango))
    return None


def _pk_de_codigo(texto):
    """«OC-2026-0004», «0004» o «4» → 4. Devuelve None si no hay ningún número."""
    numeros = re.findall(r"\d+", texto or "")
    return int(numeros[-1]) if numeros else None


def _etiqueta_dia(dia, hoy):
    if dia == hoy:
        return "Hoy"
    if dia == hoy - timedelta(days=1):
        return "Ayer"
    return dia


@rol_requerido("Administrador")
def actividad(request):
    tipo = request.GET.get("tipo", "")
    tipo = tipo if tipo in TIPOS else ""
    rango = request.GET.get("rango", RANGO_POR_DEFECTO)
    rango = rango if rango in RANGOS else RANGO_POR_DEFECTO
    usuario = request.GET.get("usuario", "")
    usuario = int(usuario) if usuario.isdigit() else None
    q = request.GET.get("q", "").strip()
    try:
        pagina = max(1, int(request.GET.get("pagina", 1)))
    except ValueError:
        pagina = 1
    desde = _desde(rango)
    tope = pagina * POR_PAGINA + 1          # uno de más para saber si hay página siguiente

    eventos = []
    if tipo in ("", "ordenes"):
        h = HistorialEstado.objects.select_related("orden", "usuario")
        if desde:
            h = h.filter(fecha__gte=desde)
        if usuario:
            h = h.filter(usuario_id=usuario)
        if q:
            h = h.filter(orden_id=_pk_de_codigo(q)) if _pk_de_codigo(q) else h.none()
        eventos += [{"tipo": "estado", "fecha": x.fecha, "h": x, "orden": x.orden} for x in h.order_by("-fecha", "-pk")[:tope]]
    if tipo in ("", "incidencias"):
        m = MensajeIncidencia.objects.select_related("autor", "incidencia__linea__orden", "incidencia__linea__producto")
        if desde:
            m = m.filter(fecha__gte=desde)
        if usuario:
            m = m.filter(autor_id=usuario)
        if q:
            m = m.filter(incidencia__linea__orden_id=_pk_de_codigo(q)) if _pk_de_codigo(q) else m.none()
        eventos += [{"tipo": "incidencia", "fecha": x.fecha, "m": x, "orden": x.incidencia.linea.orden,
                     "producto": x.incidencia.linea.producto.nombre, "incidencia": x.incidencia}
                    for x in m.order_by("-fecha", "-pk")[:tope]]
    eventos.sort(key=lambda e: e["fecha"], reverse=True)
    inicio = (pagina - 1) * POR_PAGINA
    pagina_eventos = eventos[inicio:inicio + POR_PAGINA]
    hay_siguiente = len(eventos) > inicio + POR_PAGINA

    hoy = timezone.localdate()
    dias = []
    for e in pagina_eventos:
        dia = timezone.localtime(e["fecha"]).date()
        if not dias or dias[-1]["dia"] != dia:
            dias.append({"dia": dia, "etiqueta": _etiqueta_dia(dia, hoy), "eventos": []})
        dias[-1]["eventos"].append(e)

    usuarios = User.objects.filter(Q(historialestado__isnull=False) | Q(mensajes_incidencia__isnull=False)).distinct().order_by("first_name", "username")
    parametros = request.GET.copy()
    parametros.pop("pagina", None)
    return render(request, "pedidos/actividad.html", {
        "titulo": "Actividad",
        "dias": dias, "n_eventos": len(pagina_eventos),
        "tipos": TIPOS, "rangos": RANGOS, "usuarios": usuarios,
        "tipo": tipo, "rango": rango, "usuario": usuario, "q": q,
        "hay_filtros": bool(tipo or usuario or q or rango != RANGO_POR_DEFECTO),
        "pagina": pagina, "hay_siguiente": hay_siguiente,
        "base_qs": parametros.urlencode(),
    })
