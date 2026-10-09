from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_catalogo as cat
urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("login/", auth_views.LoginView.as_view(template_name="pedidos/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/admin/", views.dashboard_admin, name="dashboard_admin"),
    path("dashboard/compras/", views.dashboard_compras, name="dashboard_compras"),
    path("dashboard/almacen/", views.dashboard_almacen, name="dashboard_almacen"),
    path("dashboard/produccion/", views.dashboard_produccion, name="dashboard_produccion"),

    # Catálogo: proveedores
    path("catalogo/proveedores/", cat.ProveedorLista.as_view(), name="proveedor_lista"),
    path("catalogo/proveedores/nuevo/", cat.ProveedorNuevo.as_view(), name="proveedor_nuevo"),
    path("catalogo/proveedores/<int:pk>/editar/", cat.ProveedorEditar.as_view(), name="proveedor_editar"),
    path("catalogo/proveedores/<int:pk>/eliminar/", cat.ProveedorEliminar.as_view(), name="proveedor_eliminar"),

    # Catálogo: productos
    path("catalogo/productos/", cat.ProductoLista.as_view(), name="producto_lista"),
    path("catalogo/productos/nuevo/", cat.ProductoNuevo.as_view(), name="producto_nuevo"),
    path("catalogo/productos/<int:pk>/editar/", cat.ProductoEditar.as_view(), name="producto_editar"),
    path("catalogo/productos/<int:pk>/eliminar/", cat.ProductoEliminar.as_view(), name="producto_eliminar"),
]
