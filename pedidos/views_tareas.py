from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services
from .catalogo_incidencias import etiqueta_accion
from .context_processors import TODOS, rol_actual
from .decorators import rol_requerido
from .forms_incidencias import AceptacionForm
from .models import Tarea

VISTAS = ("pendientes", "completadas", "todas")


@rol_requerido(*TODOS)
def tarea_lista(request):
    """Tareas del departamento del usuario. Cada departamento solo ve las suyas."""
    rol = rol_actual(request.user)
    vista = request.GET.get("vista", "pendientes")
    vista = vista if vista in VISTAS else "pendientes"
    visibles = Tarea.objects.filter(departamento=rol, estado__in=("pendiente", "completada"))
    consulta = visibles
    if vista == "pendientes":
        consulta = consulta.filter(estado="pendiente")
    elif vista == "completadas":
        consulta = consulta.filter(estado="completada")
    consulta = (consulta.select_related("incidencia__linea__orden", "incidencia__linea__producto", "creada_por",
                                        "completada_por", "orden_reposicion")
                .order_by("estado", "-visible_desde", "-pk"))
    # la visita anterior sirve para marcar las tareas nuevas; al abrir la lista se apaga el aviso del menú
    anterior = services.marcar_visita(request.user, "tareas") or request.user.date_joined
    tareas = list(consulta)
    for t in tareas:
        t.etiqueta = etiqueta_accion(t.accion)
        t.nueva = t.estado == "pendiente" and t.visible_desde is not None and t.visible_desde > anterior
        t.orden = t.incidencia.linea.orden
    return render(request, "pedidos/tareas/lista.html", {
        "titulo": f"Tareas · {rol}",
        "tareas": tareas,
        "vista": vista,
        "rol": rol,
        "n_pendientes": visibles.filter(estado="pendiente").count(),
        "n_completadas": visibles.filter(estado="completada").count(),
        "ver_incidencia": rol in services.ROLES_TICKET,
    })


@rol_requerido(*TODOS)
@require_POST
def tarea_completar(request, pk):
    tarea = get_object_or_404(Tarea, pk=pk)
    if tarea.departamento != rol_actual(request.user):
        raise PermissionDenied
    form = AceptacionForm(request.POST, prefix=f"t{pk}")
    texto = form.cleaned_data["texto"] if form.is_valid() else ""
    try:
        services.completar_tarea(tarea, request.user, texto)
    except services.SinPermiso:
        raise PermissionDenied
    except (services.TransicionInvalida, ValueError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Tarea completada. El Administrador la verificará.")
    return redirect("tarea_lista")
