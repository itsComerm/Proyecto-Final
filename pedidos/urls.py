from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("login/", auth_views.LoginView.as_view(template_name="pedidos/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/admin/", views.dashboard_admin, name="dashboard_admin"),
    path("dashboard/compras/", views.dashboard_compras, name="dashboard_compras"),
    path("dashboard/almacen/", views.dashboard_almacen, name="dashboard_almacen"),
    path("dashboard/produccion/", views.dashboard_produccion, name="dashboard_produccion"),
]

