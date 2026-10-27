from django.shortcuts import render

from .decorators import rol_requerido
from .models import OrdenCompra


@rol_requerido("Administrador")
def autorizacion_lista(request):
    """Órdenes pendientes de autorizar, de la más antigua a la más reciente, en tarjetas expandibles."""
    ordenes = list(
        OrdenCompra.objects.filter(estado="creada")
        .select_related("creado_por")
        .prefetch_related("lineas__producto__proveedor", "lineas__unidad_medida")
        .order_by("fecha_creacion", "pk")
    )
    return render(request, "pedidos/autorizaciones.html", {
        "titulo": "Autorizaciones",
        "ordenes": ordenes,
    })
