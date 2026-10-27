import re
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Case, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from . import services
from .catalogo_incidencias import etiqueta_accion, genera_reposicion
from .context_processors import rol_actual
from .decorators import rol_requerido
from .forms_incidencias import AceptacionForm, ComentarioForm, MotivoForm, ResolucionForm
from .models import Incidencia, MensajeIncidencia

ROLES_VER = services.ROLES_TICKET

# acción -> (formulario, función del servicio, mensaje de éxito)
ACCIONES = {
    "comentar": (ComentarioForm, lambda i, u, d: services.comentar_incidencia(i, u, d["texto"]), "Mensaje publicado."),
    "proponer": (ResolucionForm, lambda i, u, d: services.proponer_solucion(i, u, d["resolucion"], d["_tareas"], d.get("unidades_reposicion")),
                 "Resolución enviada. Queda pendiente de que el Administrador apruebe la medida."),
    "aceptar": (AceptacionForm, lambda i, u, d: services.aceptar_propuesta(i, u, d["texto"]),
                "Medida aprobada. Las tareas ya están en los departamentos."),
    "rechazar": (MotivoForm, lambda i, u, d: services.rechazar_propuesta(i, u, d["texto"]),
                 "Propuesta devuelta a Compras con tu motivo."),
    "cerrar": (AceptacionForm, lambda i, u, d: services.cerrar_incidencia(i, u, d["texto"]),
               "Incidencia cerrada."),
}


def _formulario(accion, incidencia, datos=None):
    clase = ACCIONES[accion][0]
    if clase is ResolucionForm:
        return clase(incidencia, datos, prefix=accion)
    return clase(datos, prefix=accion)


def _consulta():
    return Incidencia.objects.select_related(
        "linea__producto__proveedor", "linea__unidad_medida", "linea__orden", "creado_por", "gestionada_por", "resuelta_por"
    )


EDADES = {"hoy": 1, "7": 7, "30": 30}


class IncidenciaLista(ListView):
    """Panel de incidencias: las últimas con novedades arriba; filtros por vista, antigüedad, categoría y texto."""
    template_name = "pedidos/incidencias/lista.html"
    context_object_name = "incidencias"
    paginate_by = 15

    def dispatch(self, request, *args, **kwargs):
        return rol_requerido(*ROLES_VER)(super().dispatch)(request, *args, **kwargs)

    def _vista(self):
        vista = self.request.GET.get("vista", "activas")
        return vista if vista in ("accion", "activas", "resueltas", "todas") else "activas"

    def _filtros(self):
        g = self.request.GET
        edad = g.get("edad", "")
        categoria = g.get("categoria", "")
        return {
            "edad": edad if edad in ("hoy", "7", "30", "mas30") else "",
            "categoria": categoria if categoria in dict(Incidencia.CATEGORIAS) else "",
            "q": g.get("q", "").strip()[:60],
            "orden": g.get("orden") if g.get("orden") == "apertura" else "",
        }

    def get_queryset(self):
        rol = rol_actual(self.request.user)
        mia = services.q_requiere_accion(rol)
        ultimo = MensajeIncidencia.objects.filter(incidencia=OuterRef("pk")).order_by("-fecha").values("fecha")[:1]
        consulta = _consulta().prefetch_related("tareas").annotate(
            prioridad=Case(When(mia, then=Value(0)), default=Value(1), output_field=IntegerField()),
            hay_actualizaciones=services.incidencias_con_actualizaciones(self.request.user),
            ultima_actualizacion=Coalesce(Subquery(ultimo), "fecha_creacion"),
        )
        vista = self._vista()
        if vista == "accion":
            consulta = consulta.filter(mia)
        elif vista == "activas":
            consulta = consulta.filter(resuelta=False)
        elif vista == "resueltas":
            consulta = consulta.filter(resuelta=True)

        f = self._filtros()
        ahora = timezone.now()
        if f["edad"] in EDADES:
            consulta = consulta.filter(fecha_creacion__gte=ahora - timedelta(days=EDADES[f["edad"]]))
        elif f["edad"] == "mas30":
            consulta = consulta.filter(fecha_creacion__lt=ahora - timedelta(days=30))
        if f["categoria"]:
            consulta = consulta.filter(categoria=f["categoria"])
        if f["q"]:
            texto = Q(linea__producto__nombre__icontains=f["q"]) | Q(linea__producto__proveedor__nombre__icontains=f["q"])
            codigo = re.search(r"(\d+)\s*$", f["q"])
            if codigo:
                texto |= Q(linea__orden_id=int(codigo.group(1)))
            consulta = consulta.filter(texto)
        if f["orden"] == "apertura":
            return consulta.order_by("fecha_creacion", "pk")
        return consulta.order_by("-ultima_actualizacion", "-pk")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        rol = rol_actual(self.request.user)
        for i in contexto["incidencias"]:
            i.espera_a = services.rol_que_debe_actuar(i)
            i.requiere_mi_accion = i.prioridad == 0
        f = self._filtros()
        # al abrir el listado se apaga el aviso de novedades del menú (las filas siguen marcadas hasta abrir cada una)
        services.marcar_visita(self.request.user, "incidencias")
        contexto.update(
            titulo="Incidencias",
            vista=self._vista(),
            filtros=f,
            hay_filtros=any(f[k] for k in ("edad", "categoria", "q")),
            categorias=Incidencia.CATEGORIAS,
            n_accion=services.contar_acciones_pendientes(rol),
            n_activas=Incidencia.objects.filter(resuelta=False).count(),
            n_resueltas=Incidencia.objects.filter(resuelta=True).count(),
            n_todas=Incidencia.objects.count(),
        )
        # parámetros que se conservan al cambiar de vista o de página
        restos = self.request.GET.copy()
        for clave in ("vista", "page"):
            restos.pop(clave, None)
        contexto["qs_filtros"] = restos.urlencode()
        quitar_page = self.request.GET.copy()
        quitar_page.pop("page", None)
        contexto["qs_pagina"] = quitar_page.urlencode()
        return contexto


def _render_detalle(request, incidencia, formularios_con_error=None, status=200, visto_hasta=None):
    acciones = services.acciones_disponibles(incidencia, request.user)
    formularios = {a: _formulario(a, incidencia) for a in acciones}
    formularios.update(formularios_con_error or {})
    repone = genera_reposicion(incidencia.resolucion)
    fases = [f for f, _ in Incidencia.FASES if f != "reposicion" or repone or incidencia.fase == "reposicion"]
    paso_actual = fases.index(incidencia.fase)
    etiquetas = dict(Incidencia.FASES)
    mensajes = list(incidencia.mensajes.select_related("autor"))
    for m in mensajes:
        # "Nuevo": mensaje de otra persona posterior a la última vez que abriste el ticket
        m.es_nuevo = visto_hasta is not None and m.pk > visto_hasta and m.autor_id != request.user.id
    propuestas = [m for m in mensajes if m.tipo == "propuesta"]
    orden = incidencia.linea.orden
    tareas = [t for t in incidencia.tareas.select_related("completada_por", "orden_reposicion") if t.estado != "borrador"]
    borradores = [t for t in incidencia.tareas.all() if t.estado == "borrador"]
    for t in tareas + borradores:
        t.etiqueta = etiqueta_accion(t.accion)
    es_admin = rol_actual(request.user) == "Administrador"
    return render(request, "pedidos/incidencias/detalle.html", {
        "titulo": f"Incidencia #{incidencia.pk}",
        "incidencia": incidencia,
        "orden": orden,
        "proveedor": orden.proveedor,
        "lineas_orden": orden.lineas.select_related("producto", "unidad_medida").order_by("pk"),
        "propuesta": propuestas[-1] if propuestas else None,
        "tareas": tareas,
        "borradores": borradores,
        "reposiciones": incidencia.reposiciones.all(),
        "puede_reabrir": es_admin and incidencia.fase in ("aceptada", "reposicion", "solucionada"),
        "n_nuevos": sum(m.es_nuevo for m in mensajes),
        "mensajes": mensajes,
        "acciones": acciones,
        "formularios": formularios,
        "espera_a": services.rol_que_debe_actuar(incidencia),
        "requiere_mi_accion": any(a != "comentar" for a in acciones),
        "pasos": [{"clave": c, "etiqueta": etiquetas[c], "estado": "hecho" if n < paso_actual else ("actual" if n == paso_actual else "pendiente")}
                  for n, c in enumerate(fases)],
    }, status=status)


@rol_requerido(*ROLES_VER)
def incidencia_detalle(request, pk):
    incidencia = get_object_or_404(_consulta(), pk=pk)
    visto_hasta = services.marcar_incidencia_vista(incidencia, request.user)
    return _render_detalle(request, incidencia, visto_hasta=visto_hasta)


@rol_requerido(*ROLES_VER)
@require_POST
def incidencia_accion(request, pk, accion):
    if accion not in ACCIONES:
        raise PermissionDenied
    incidencia = get_object_or_404(_consulta(), pk=pk)
    if not services.rol_puede(accion, incidencia, request.user):
        raise PermissionDenied

    _, ejecutar, mensaje_ok = ACCIONES[accion]
    form = _formulario(accion, incidencia, request.POST)
    if not form.is_valid():
        return _render_detalle(request, incidencia, {accion: form})
    datos = dict(form.cleaned_data)
    if accion == "proponer":
        datos["_tareas"] = form.tareas()
    try:
        resultado = ejecutar(incidencia, request.user, datos)
    except services.SinPermiso:
        raise PermissionDenied
    except (services.TransicionInvalida, ValueError) as error:
        messages.error(request, str(error))
    else:
        if accion == "cerrar" and resultado[1]:
            mensaje_ok = f"Incidencia cerrada. La orden {incidencia.linea.orden.codigo} queda como Recibida."
        elif accion == "cerrar":
            mensaje_ok = "Incidencia cerrada. La orden aún tiene otras incidencias abiertas."
        elif accion == "aceptar" and resultado.resuelta:
            mensaje_ok = "Medida aprobada. No requería tareas: la incidencia queda cerrada."
        messages.success(request, mensaje_ok)
    return redirect("incidencia_detalle", pk=pk)


@rol_requerido("Administrador")
@require_POST
def tarea_reabrir(request, pk, tarea_pk):
    incidencia = get_object_or_404(_consulta(), pk=pk)
    tarea = get_object_or_404(incidencia.tareas, pk=tarea_pk)
    form = MotivoForm(request.POST, prefix=f"reabrir{tarea_pk}")
    if not form.is_valid():
        messages.error(request, "Indica qué falta por hacer para reabrir la tarea.")
        return redirect("incidencia_detalle", pk=pk)
    try:
        services.reabrir_tarea(tarea, request.user, form.cleaned_data["texto"])
    except services.SinPermiso:
        raise PermissionDenied
    except (services.TransicionInvalida, ValueError) as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Tarea reabierta. Vuelve a su departamento con tu motivo.")
    return redirect("incidencia_detalle", pk=pk)
