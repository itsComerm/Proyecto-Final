from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import ListView

from . import services
from .decorators import rol_requerido
from .forms_recepcion import RecepcionForm
from .models import OrdenCompra

ROLES_RECEPCION = ("Almacén", "Administrador")


class RecepcionLista(ListView):
    """Órdenes en camino pendientes de verificar, las más antiguas primero."""
    template_name = "pedidos/recepcion/lista.html"
    context_object_name = "ordenes"

    def dispatch(self, request, *args, **kwargs):
        return rol_requerido(*ROLES_RECEPCION)(super().dispatch)(request, *args, **kwargs)

    def get_queryset(self):
        return (OrdenCompra.objects.filter(estado__in=services.ESTADOS_RECIBIBLES)
                .select_related("creado_por", "origen__linea__orden")
                .prefetch_related("lineas__producto__proveedor")
                .order_by("fecha_creacion", "pk"))

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        for orden in contexto["ordenes"]:
            orden.pendientes = len(orden.lineas.all())
        contexto["titulo"] = "Recepción"
        return contexto


@rol_requerido(*ROLES_RECEPCION)
def recepcion_verificar(request, pk):
    orden = get_object_or_404(
        OrdenCompra.objects.select_related("creado_por", "origen__linea__orden")
        .prefetch_related("lineas__producto__proveedor", "lineas__unidad_medida"),
        pk=pk,
    )
    if orden.estado not in services.ESTADOS_RECIBIBLES:
        messages.error(request, f"La orden {orden.codigo} no está pendiente de recepción.")
        return redirect("recepcion_lista")

    lineas = services.lineas_a_verificar(orden)
    form = RecepcionForm(lineas, request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            orden, incidencias = services.registrar_recepcion(orden, request.user, form.resultados())
        except services.TransicionInvalida as error:
            messages.error(request, str(error))
            return redirect("recepcion_lista")
        if incidencias:
            messages.warning(
                request,
                f"Orden {orden.codigo} registrada con {incidencias} incidencia{'s' if incidencias != 1 else ''}. "
                "Queda pendiente de gestión.",
            )
        else:
            messages.success(request, f"Orden {orden.codigo} recibida correctamente.")
        return redirect("recepcion_lista")

    return render(request, "pedidos/recepcion/verificar.html", {
        "titulo": f"Verificar {orden.codigo}",
        "orden": orden,
        "form": form,
        "filas": form.filas(),
    })
