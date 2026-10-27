from django.contrib import messages
from django.http import Http404
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from . import services
from .context_processors import rol_actual
from .decorators import rol_requerido
from .forms_ordenes import LineaFormSet, OrdenForm
from .models import OrdenCompra, Proveedor

TODOS_LOS_ROLES = ("Administrador", "Compras", "Almacén", "Producción")


class OrdenesMixin:
    """Los cuatro roles pueden consultar las órdenes (matriz de permisos)."""

    def dispatch(self, request, *args, **kwargs):
        return rol_requerido(*TODOS_LOS_ROLES)(super().dispatch)(request, *args, **kwargs)


class OrdenLista(ListView):
    model = OrdenCompra
    template_name = "pedidos/ordenes/lista.html"
    context_object_name = "ordenes"
    paginate_by = 15

    def dispatch(self, request, *args, **kwargs):
        # Almacén no ve el listado: trabaja desde Recepción e Incidencias
        return rol_requerido("Administrador", "Compras", "Producción")(super().dispatch)(request, *args, **kwargs)

    def get_queryset(self):
        consulta = (
            OrdenCompra.objects.select_related("creado_por", "origen__linea__orden")
            .prefetch_related("lineas__producto__proveedor")
            .annotate(num_lineas=Count("lineas", distinct=True))
            .order_by("-fecha_creacion", "-pk")
        )
        estado = self.request.GET.get("estado", "")
        proveedor = self.request.GET.get("proveedor", "")
        if rol_actual(self.request.user) == "Producción":
            consulta = consulta.exclude(estado="creada")        # lo pendiente de autorizar no le concierne
        if estado in dict(OrdenCompra.ESTADOS):
            consulta = consulta.filter(estado=estado)
        if proveedor.isdigit():
            consulta = consulta.filter(lineas__producto__proveedor_id=proveedor).distinct()
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        estado = self.request.GET.get("estado", "")
        proveedor = self.request.GET.get("proveedor", "")
        parametros = self.request.GET.copy()
        parametros.pop("page", None)
        contexto.update(
            titulo="Órdenes de compra",
            estados=[e for e in OrdenCompra.ESTADOS
                     if not (e[0] == "creada" and rol_actual(self.request.user) == "Producción")],
            lista_proveedores=Proveedor.objects.order_by("nombre"),
            estado_sel=estado,
            proveedor_sel=proveedor,
            hay_filtros=bool(estado or proveedor),
            querystring=parametros.urlencode(),
        )
        return contexto


class OrdenDetalle(OrdenesMixin, DetailView):
    template_name = "pedidos/ordenes/detalle.html"
    context_object_name = "orden"
    queryset = OrdenCompra.objects.select_related("creado_por", "origen__linea__orden").prefetch_related(
        "lineas__producto__proveedor", "lineas__unidad_medida", "lineas__incidencias__creado_por",
        "lineas__incidencias__mensajes__autor", "historial__usuario"
    )

    def get_object(self, queryset=None):
        orden = super().get_object(queryset)
        # Almacén solo ve órdenes que ya han salido del proveedor (no las pendientes de autorizar)
        if rol_actual(self.request.user) in ("Almacén", "Producción") and orden.estado == "creada":
            raise Http404
        return orden

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = self.object.codigo
        contexto["incidencias"] = [
            (linea, incidencia)
            for linea in self.object.lineas.all()
            for incidencia in linea.incidencias.all()
        ]
        contexto["actividad"] = self._actividad(self.object, contexto["incidencias"])
        return contexto

    @staticmethod
    def _actividad(orden, incidencias):
        """Línea de tiempo: cambios de estado + hitos de cada ticket (propuesta, aceptación, ejecución, cierre…)."""
        eventos = [{"tipo": "estado", "fecha": h.fecha, "h": h} for h in orden.historial.all()]
        for linea, i in incidencias:
            for m in i.mensajes.all():
                if m.tipo != "comentario":                          # los comentarios viven solo en el ticket
                    eventos.append({"tipo": "incidencia", "fecha": m.fecha, "m": m, "producto": linea.producto.nombre})

        def orden_en_empate(e):
            # dentro del mismo segundo: primero la verificación que abre incidencias, luego los hitos del ticket y,
            # al final, el cambio de estado que provoca el cierre
            if e["tipo"] == "estado":
                h = e["h"]
                return 2 if (h.estado_nuevo == "recibido" and h.estado_anterior != "en_camino") else 0
            return 1
        return sorted(eventos, key=lambda e: (e["fecha"].replace(microsecond=0), orden_en_empate(e), e["fecha"]))


def _lineas_de_otro_proveedor(proveedor, formset):
    """Marca como error los productos que no son del proveedor elegido."""
    hay_error = False
    for form in formset.forms:
        if formset._should_delete_form(form) or not form.cleaned_data:
            continue
        producto = form.cleaned_data.get("producto")
        if producto is not None and producto.proveedor_id != proveedor.pk:
            form.add_error("producto", "Este producto no pertenece al proveedor elegido.")
            hay_error = True
    return hay_error


@rol_requerido("Compras", "Administrador")
def orden_nueva(request):
    form = OrdenForm(request.POST or None)
    formset = LineaFormSet(request.POST or None, instance=OrdenCompra(), prefix="lineas")

    if request.method == "POST":
        form_ok = form.is_valid()
        formset_ok = formset.is_valid()
        if form_ok and formset_ok:
            if not _lineas_de_otro_proveedor(form.cleaned_data["proveedor"], formset):
                orden = services.crear_orden(usuario=request.user, form=form, formset=formset)
                messages.success(request, f"Orden {orden.codigo} creada. Queda pendiente de autorización.")
                return redirect("orden_detalle", pk=orden.pk)

    return render(request, "pedidos/ordenes/nueva.html", {
        "titulo": "Nueva orden de compra", "form": form, "formset": formset,
    })


def _volver(request, pk):
    """Tras autorizar se vuelve a donde estaba el administrador (solo destinos conocidos)."""
    if request.POST.get("next") == "autorizaciones":
        return redirect("autorizacion_lista")
    return redirect("orden_detalle", pk=pk)


@rol_requerido("Administrador")
@require_POST
def orden_autorizar(request, pk):
    orden = get_object_or_404(OrdenCompra, pk=pk)
    try:
        services.autorizar_orden(orden, request.user)
    except services.TransicionInvalida as error:
        messages.error(request, str(error))
        return _volver(request, pk)

    orden = (OrdenCompra.objects
             .prefetch_related("lineas__producto__proveedor", "lineas__unidad_medida").get(pk=pk))
    try:
        services.enviar_email_orden(orden)
    except Exception:
        messages.warning(
            request,
            f"La orden {orden.codigo} está autorizada, pero no se pudo enviar el email al proveedor. "
            "Revisa su dirección de correo.",
        )
    else:
        messages.success(
            request, f"Orden {orden.codigo} autorizada y enviada a {orden.proveedor.email}."
        )
    return _volver(request, pk)
