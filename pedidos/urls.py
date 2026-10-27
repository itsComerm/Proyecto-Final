from django.contrib.auth import views as auth_views
from django.urls import path

from . import views, views_catalogo as cat, views_ordenes as ordenes, views_recepcion as recepcion, views_incidencias as incidencias, views_dashboard as dashboards, views_autorizaciones as autorizaciones, views_produccion as prod, views_ajustes as ajustes, views_actividad as actividad, views_tareas as tareas

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("login/", auth_views.LoginView.as_view(template_name="pedidos/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("actividad/", actividad.actividad, name="actividad"),
    path("dashboard/admin/", dashboards.dashboard_admin, name="dashboard_admin"),
    path("dashboard/compras/", dashboards.dashboard_compras, name="dashboard_compras"),
    path("dashboard/almacen/", dashboards.dashboard_almacen, name="dashboard_almacen"),
    path("dashboard/produccion/", prod.dashboard_produccion, name="dashboard_produccion"),

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

    # Órdenes de compra
    path("ordenes/", ordenes.OrdenLista.as_view(), name="orden_lista"),
    path("ordenes/nueva/", ordenes.orden_nueva, name="orden_nueva"),
    path("ordenes/<int:pk>/", ordenes.OrdenDetalle.as_view(), name="orden_detalle"),
    path("ordenes/<int:pk>/autorizar/", ordenes.orden_autorizar, name="orden_autorizar"),

    # Producción: materia prima y notificaciones
    path("produccion/materia-prima/", prod.materia_prima, name="materia_prima"),
    path("produccion/materia-prima/sugerencias/", prod.materia_prima_sugerencias, name="materia_prima_sugerencias"),
    path("notificaciones/", prod.notificacion_lista, name="notificacion_lista"),
    path("notificaciones/<int:pk>/leer/", prod.notificacion_leer, name="notificacion_leer"),
    path("notificaciones/leer-todas/", prod.notificacion_leer_todas, name="notificacion_leer_todas"),

    # Ajustes (Administrador)
    path("ajustes/email/", ajustes.ajustes_email, name="ajustes_email"),
    path("ajustes/email/vista-previa/", ajustes.ajustes_email_vista_previa, name="ajustes_email_vista_previa"),

    # Autorizaciones (Administrador)
    path("autorizaciones/", autorizaciones.autorizacion_lista, name="autorizacion_lista"),

    # Recepción en Almacén
    path("recepcion/", recepcion.RecepcionLista.as_view(), name="recepcion_lista"),
    path("recepcion/<int:pk>/", recepcion.recepcion_verificar, name="recepcion_verificar"),

    # Incidencias
    path("incidencias/", incidencias.IncidenciaLista.as_view(), name="incidencia_lista"),
    path("incidencias/<int:pk>/", incidencias.incidencia_detalle, name="incidencia_detalle"),
    path("incidencias/<int:pk>/tareas/<int:tarea_pk>/reabrir/", incidencias.tarea_reabrir, name="tarea_reabrir"),
    path("incidencias/<int:pk>/<slug:accion>/", incidencias.incidencia_accion, name="incidencia_accion"),

    # Tareas por departamento
    path("tareas/", tareas.tarea_lista, name="tarea_lista"),
    path("tareas/<int:pk>/completar/", tareas.tarea_completar, name="tarea_completar"),
]
