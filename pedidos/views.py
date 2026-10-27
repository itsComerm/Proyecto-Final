from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .decorators import rol_requerido

DASHBOARD_POR_GRUPO = {"Administrador": "dashboard_admin", "Compras": "dashboard_compras", "Almacén": "dashboard_almacen", "Producción": "dashboard_produccion"}

@login_required
def inicio(request):
    for grupo in request.user.groups.values_list("name", flat=True):
        if grupo in DASHBOARD_POR_GRUPO:
            return redirect(DASHBOARD_POR_GRUPO[grupo])
    if request.user.is_superuser:
        return redirect("admin:index")
    return render(request, "pedidos/sin_rol.html")

def _dashboard(request, titulo):
    return render(request, "pedidos/dashboard.html", {"titulo": titulo})

@rol_requerido("Administrador")
def dashboard_admin(request): return _dashboard(request, "Dashboard Administrador")
@rol_requerido("Almacén")
def dashboard_almacen(request): return _dashboard(request, "Dashboard Almacén")
@rol_requerido("Producción")
def dashboard_produccion(request): return _dashboard(request, "Dashboard Producción")
