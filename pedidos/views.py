from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .decorators import rol_requerido

DASHBOARD_POR_GRUPO = {
    "Administrador": "dashboard_admin",
    "Compras": "dashboard_compras",
    "Almacén": "dashboard_almacen",
    "Producción": "dashboard_produccion",
}


@login_required
def inicio(request):
    """Envía a cada usuario al dashboard de su rol."""
    for grupo, nombre_url in DASHBOARD_POR_GRUPO.items():
        if request.user.groups.filter(name=grupo).exists():
            return redirect(nombre_url)
    if request.user.is_superuser:
        return redirect("admin:index")
    return render(request, "pedidos/sin_rol.html")


def _dashboard(request, titulo):
    return render(request, "pedidos/dashboard.html", {"titulo": titulo})


@rol_requerido("Administrador")
def dashboard_admin(request):
    return _dashboard(request, "Dashboard Administrador")


@rol_requerido("Compras")
def dashboard_compras(request):
    return _dashboard(request, "Dashboard Compras")


@rol_requerido("Almacén")
def dashboard_almacen(request):
    return _dashboard(request, "Dashboard Almacén")


@rol_requerido("Producción")
def dashboard_produccion(request):
    return _dashboard(request, "Dashboard Producción")