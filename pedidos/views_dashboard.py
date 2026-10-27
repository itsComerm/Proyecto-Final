from datetime import timedelta

from django.db.models import Avg, Case, Count, IntegerField, Value, When, DurationField, ExpressionWrapper, F, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import TruncWeek
from django.shortcuts import render
from django.utils import timezone
from django.utils.timesince import timesince

from . import services
from .decorators import rol_requerido
from .models import HistorialEstado, Incidencia, MensajeIncidencia, OrdenCompra

FILAS = 8        # filas de cada tabla del dashboard


def _incidencias_abiertas(rol, solo_accion=False):
    """Incidencias abiertas (todas por defecto); las que esperan a `rol` van primero y se marcan."""
    mia = services.q_requiere_accion(rol)
    consulta = Incidencia.objects.filter(resuelta=False)
    if solo_accion:
        consulta = consulta.filter(mia)
    incidencias = list(
        consulta.select_related("linea__producto", "linea__orden", "creado_por")
        .annotate(prioridad=Case(When(mia, then=Value(0)), default=Value(1), output_field=IntegerField()))
        .order_by("prioridad", "fecha_creacion", "pk")[:FILAS])
    for i in incidencias:
        i.espera_a = services.rol_que_debe_actuar(i)
        i.requiere_mi_accion = i.prioridad == 0
    return incidencias


def _tarjetas_almacen():
    mas_antigua = (HistorialEstado.objects
                   .filter(estado_nuevo="en_camino", orden__estado="en_camino")
                   .order_by("fecha").first())
    return {
        "en_camino": OrdenCompra.objects.filter(estado="en_camino").count(),
        "mas_antigua": mas_antigua,
        "incidencias_abiertas": services.contar_incidencias_abiertas(),
        "requieren_accion": services.contar_acciones_pendientes("Almacén"),
    }


def _por_recibir():
    """Órdenes en camino, las que llevan más tiempo esperando primero."""
    desde = (HistorialEstado.objects.filter(orden=OuterRef("pk"), estado_nuevo="en_camino")
             .order_by("-fecha").values("fecha")[:1])
    return list(OrdenCompra.objects.filter(estado="en_camino")
                .annotate(en_camino_desde=Subquery(desde))
                .prefetch_related("lineas__producto__proveedor")
                .order_by("en_camino_desde", "pk")[:FILAS])


@rol_requerido("Almacén")
def dashboard_almacen(request):
    solo_accion = request.GET.get("incidencias") == "accion"
    return render(request, "pedidos/dashboard_almacen.html", {
        "titulo": "Dashboard Almacén",
        "filtro_incidencias": "accion" if solo_accion else "abiertas",
        "t": _tarjetas_almacen(),
        "por_recibir": _por_recibir(),
        "incidencias": _incidencias_abiertas("Almacén", solo_accion),
    })


# ======================= Administrador =======================
def _tarjetas_admin():
    creadas = OrdenCompra.objects.filter(estado="creada")
    en_camino_antigua = (HistorialEstado.objects.filter(estado_nuevo="en_camino", orden__estado="en_camino")
                         .order_by("fecha").first())
    return {
        "por_autorizar": creadas.count(),
        "pendiente_mas_antigua": creadas.order_by("fecha_creacion").first(),
        "en_camino": OrdenCompra.objects.filter(estado="en_camino").count(),
        "en_camino_mas_antigua": en_camino_antigua,
        "incidencias_admin": services.contar_acciones_pendientes("Administrador"),
        "incidencias_abiertas": services.contar_incidencias_abiertas(),
    }


def _pendientes_de_autorizar():
    return list(OrdenCompra.objects.filter(estado="creada").select_related("creado_por")
                .prefetch_related("lineas__producto__proveedor").order_by("fecha_creacion", "pk")[:5])


def _incidencias_del_admin(solo_accion=False):
    return _incidencias_abiertas("Administrador", solo_accion)


def _actividad_reciente():
    return list(HistorialEstado.objects.select_related("orden", "usuario").order_by("-fecha", "-pk")[:5])


@rol_requerido("Administrador")
def dashboard_admin(request):
    solo_accion = request.GET.get("incidencias") == "accion"
    return render(request, "pedidos/dashboard_admin.html", {
        "titulo": "Dashboard Administrador",
        "filtro_incidencias": "accion" if solo_accion else "abiertas",
        "t": _tarjetas_admin(),
        "pendientes": _pendientes_de_autorizar(),
        "incidencias": _incidencias_del_admin(solo_accion),
        "actividad": _actividad_reciente(),
    })


# ======================= Compras =======================
ESTADOS_POR_CERRAR = ("creada", "en_camino", "incidencia")


def _tarjetas_compras():
    mas_antigua = (HistorialEstado.objects.filter(estado_nuevo="en_camino", orden__estado="en_camino")
                   .order_by("fecha").first())
    return {
        "en_camino": OrdenCompra.objects.filter(estado="en_camino").count(),
        "mas_antigua": mas_antigua,
        "incidencias_abiertas": services.contar_incidencias_abiertas(),
        "requieren_accion": services.contar_acciones_pendientes("Compras"),
        "por_cerrar": OrdenCompra.objects.filter(estado__in=ESTADOS_POR_CERRAR).count(),
        "ordenes_total": OrdenCompra.objects.count(),
    }


def _ordenes_compras(todas=False):
    """Por defecto, las órdenes sin cerrar de cualquier usuario; con `todas`, también las ya completadas."""
    consulta = OrdenCompra.objects.select_related("creado_por").prefetch_related("lineas__producto__proveedor")
    if not todas:
        consulta = consulta.filter(estado__in=ESTADOS_POR_CERRAR)
    return list(consulta.order_by("-fecha_creacion", "-pk")[:FILAS])


@rol_requerido("Compras")
def dashboard_compras(request):
    todas = request.GET.get("ordenes") == "todas"
    solo_accion = request.GET.get("incidencias") == "accion"
    return render(request, "pedidos/dashboard_compras.html", {
        "titulo": "Dashboard Compras",
        "filtro_ordenes": "todas" if todas else "por_cerrar",
        "filtro_incidencias": "accion" if solo_accion else "abiertas",
        "t": _tarjetas_compras(),
        "ordenes": _ordenes_compras(todas),
        "incidencias": _incidencias_abiertas("Compras", solo_accion),
    })
