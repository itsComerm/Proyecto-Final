import unicodedata
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .decorators import rol_requerido
from .models import HistorialEstado, LineaPedido, Notificacion, OrdenCompra, Producto

FILAS = 8
ESTADOS_CON_MATERIAL = ("en_camino", "incidencia", "incidencia_resuelta", "recibido")


def _normalizar(texto):
    """Minúsculas y sin acentos, para que «acido» encuentre «Ácido»."""
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _estado_linea(linea):
    """(tono, etiqueta) de una línea según su verificación y sus incidencias."""
    if linea.resultado_verificacion == "ok":
        return "received", "Recibida"
    if linea.resultado_verificacion == "ng":
        incidencias = list(linea.incidencias.all())
        if incidencias and all(i.resuelta for i in incidencias):
            return "approved", "Incidencia resuelta"
        return "incident", "Con incidencia"
    return "transit", "En camino"


def _decorar(orden):
    for linea in orden.lineas.all():
        linea.tono, linea.etiqueta = _estado_linea(linea)
    return orden


def _ordenes_en_camino():
    ordenes = list(
        OrdenCompra.objects.filter(estado="en_camino")
        .prefetch_related("lineas__producto__proveedor", "lineas__unidad_medida", "lineas__incidencias", "historial")
    )
    for o in ordenes:
        o.salida = next((h.fecha for h in o.historial.all() if h.estado_nuevo == "en_camino"), None)
        o.modo = "camino"
        _decorar(o)
    return sorted(ordenes, key=lambda o: (o.salida is None, o.salida))[:FILAS]


def _llegadas():
    ordenes = OrdenCompra.objects.prefetch_related(
        "lineas__producto__proveedor", "lineas__unidad_medida", "lineas__incidencias")
    eventos = list(
        HistorialEstado.objects
        .filter(estado_anterior="en_camino", estado_nuevo__in=("recibido", "incidencia"))
        .prefetch_related(Prefetch("orden", queryset=ordenes))
        .order_by("-fecha", "-pk")[:FILAS]
    )
    resultado = []
    for e in eventos:
        o = _decorar(e.orden)
        o.llegada = e.fecha
        # lo que importa a Producción es cómo está ahora: con incidencia abierta, resuelta, o todo OK desde el principio
        o.resultado = "incidencia" if o.estado == "incidencia" else ("resuelta" if e.estado_nuevo == "incidencia" else "ok")
        o.modo = "llegada"
        resultado.append(o)
    return resultado


@rol_requerido("Producción")
def dashboard_produccion(request):
    en_camino = _ordenes_en_camino()
    ultimos_avisos = list(
        Notificacion.objects.filter(destinatario=request.user)
        .select_related("orden", "incidencia__linea__producto")
        .prefetch_related("orden__lineas__producto__proveedor")[:5]
    )
    return render(request, "pedidos/dashboard_produccion.html", {
        "titulo": "Dashboard Producción",
        "t": {
            "en_camino": OrdenCompra.objects.filter(estado="en_camino").count(),
            "con_incidencia": OrdenCompra.objects.filter(estado="incidencia").count(),
            "sin_leer": services.contar_notificaciones_sin_leer(request.user),
        },
        "en_camino": en_camino,
        "llegadas": _llegadas(),
        "avisos": ultimos_avisos,
    })


PERIODOS = [("7", "Últimos 7 días", 7), ("30", "Último mes", 30), ("90", "Últimos 3 meses", 90), ("todo", "Todo", None)]


def _periodo(request):
    clave = request.GET.get("dias", "30")
    if clave not in {c for c, _, _ in PERIODOS}:
        clave = "30"
    dias = next(d for c, _, d in PERIODOS if c == clave)
    return clave, (timezone.now() - timedelta(days=dias) if dias else None)


def _lineas_con_fecha():
    """Líneas de órdenes ya autorizadas, con la fecha de referencia: cuándo llegó o, si no, cuándo salió."""
    llegada = (HistorialEstado.objects.filter(orden=OuterRef("orden_id"), estado_anterior="en_camino",
                                              estado_nuevo__in=("recibido", "incidencia")).order_by("fecha").values("fecha")[:1])
    salida = HistorialEstado.objects.filter(orden=OuterRef("orden_id"), estado_nuevo="en_camino").order_by("fecha").values("fecha")[:1]
    return (LineaPedido.objects.filter(orden__estado__in=ESTADOS_CON_MATERIAL)
            .annotate(llegada=Subquery(llegada), salida=Subquery(salida))
            .annotate(fecha_ref=Coalesce("llegada", "salida", "orden__fecha_creacion")))


def _productos_que_coinciden(q):
    clave = _normalizar(q)
    return [p for p in Producto.objects.select_related("proveedor").order_by("nombre") if clave in _normalizar(p.nombre)]


@rol_requerido("Producción")
def materia_prima(request):
    """¿Qué órdenes contienen esta materia prima y en qué situación están? (no es un control de stock)"""
    q = request.GET.get("q", "").strip()
    producto_id = request.GET.get("producto", "")
    periodo, desde = _periodo(request)

    productos = []
    if producto_id.isdigit():
        productos = list(Producto.objects.select_related("proveedor").filter(pk=producto_id))
    elif q:
        productos = _productos_que_coinciden(q)
    buscado = bool(productos) or bool(q) or producto_id.isdigit()

    lineas = []
    if productos:
        consulta = _lineas_con_fecha().filter(producto__in=productos)
        if desde:
            consulta = consulta.filter(fecha_ref__gte=desde)
        lineas = list(consulta.select_related("orden", "producto__proveedor", "unidad_medida")
                      .prefetch_related("incidencias").order_by("-fecha_ref", "-pk")[:200])
        for l in lineas:
            l.tono, l.etiqueta = _estado_linea(l)

    titulo_busqueda = productos[0].nombre if producto_id.isdigit() and productos else q
    return render(request, "pedidos/materia_prima.html", {
        "titulo": "Materia prima", "q": q, "producto_id": producto_id, "titulo_busqueda": titulo_busqueda,
        "buscado": buscado, "productos": productos, "lineas": lineas,
        "periodo": periodo, "periodos": [(c, e) for c, e, _ in PERIODOS],
        "n_ordenes": len({l.orden_id for l in lineas}),
    })


@rol_requerido("Producción")
def materia_prima_sugerencias(request):
    """JSON para el buscador en vivo: productos que coinciden y en cuántas órdenes aparecen en el periodo."""
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse({"resultados": []})
    _, desde = _periodo(request)
    productos = _productos_que_coinciden(q)[:8]
    consulta = _lineas_con_fecha().filter(producto__in=productos)
    if desde:
        consulta = consulta.filter(fecha_ref__gte=desde)
    cuentas = dict(consulta.values_list("producto_id").annotate(n=Count("orden", distinct=True)))
    return JsonResponse({"resultados": [
        {"id": p.pk, "nombre": p.nombre, "proveedor": p.proveedor.nombre, "ordenes": cuentas.get(p.pk, 0)}
        for p in productos
    ]})


# ---------------- Notificaciones ----------------
@rol_requerido("Producción")
def notificacion_lista(request):
    vista = request.GET.get("vista", "sin_leer")
    if vista not in ("sin_leer", "todas"):
        vista = "sin_leer"
    propias = Notificacion.objects.filter(destinatario=request.user)
    sin_leer = propias.filter(leida=False).count()
    consulta = propias.filter(leida=False) if vista == "sin_leer" else propias
    avisos = list(
        consulta.select_related("orden", "incidencia__linea__producto")
        .prefetch_related("orden__lineas__producto__proveedor")[:50]
    )
    return render(request, "pedidos/notificaciones.html", {
        "titulo": "Notificaciones", "avisos": avisos, "vista": vista,
        "n_sin_leer": sin_leer, "n_todas": propias.count(),
    })


def _volver_a_lista(request):
    vista = request.POST.get("vista")
    return redirect(reverse("notificacion_lista") + (f"?vista={vista}" if vista in ("sin_leer", "todas") else ""))


@rol_requerido("Producción")
@require_POST
def notificacion_leer(request, pk):
    aviso = get_object_or_404(Notificacion, pk=pk, destinatario=request.user)     # solo las propias
    if not aviso.leida:
        aviso.leida = True
        aviso.save(update_fields=["leida"])
    if request.POST.get("next") == "orden":
        return redirect("orden_detalle", pk=aviso.orden_id)
    return _volver_a_lista(request)


@rol_requerido("Producción")
@require_POST
def notificacion_leer_todas(request):
    n = Notificacion.objects.filter(destinatario=request.user, leida=False).update(leida=True)
    if n:
        messages.success(request, f"{n} aviso{'s' if n != 1 else ''} marcado{'s' if n != 1 else ''} como leído{'s' if n != 1 else ''}.")
    return _volver_a_lista(request)
