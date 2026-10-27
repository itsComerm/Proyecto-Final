from django.urls import reverse

ROL_DASHBOARD = {
    "Administrador": "dashboard_admin",
    "Compras": "dashboard_compras",
    "Almacén": "dashboard_almacen",
    "Producción": "dashboard_produccion",
}
ROLES_INCIDENCIAS = {"Administrador", "Compras", "Almacén"}
TODOS = {"Administrador", "Compras", "Almacén", "Producción"}

# (etiqueta, icono, nombre de URL o None si aún no existe, roles que lo ven)
MENU = [
    ("Operativa", [
        ("Dashboard", "home", "__dashboard__", TODOS),
        ("Autorizaciones", "check", "autorizacion_lista", {"Administrador"}),
        ("Órdenes de compra", "doc", "orden_lista", TODOS - {"Almacén"}),
        ("Recepción", "inbox", "recepcion_lista", {"Administrador", "Almacén"}),
        ("Materia prima", "search", "materia_prima", {"Producción"}),
        ("Notificaciones", "bell", "notificacion_lista", {"Producción"}),
        ("Incidencias", "alert", "incidencia_lista", {"Administrador", "Compras", "Almacén"}),
        ("Tareas", "check", "tarea_lista", TODOS),
    ]),
    ("Catálogo", [
        ("Productos", "box", "producto_lista", {"Administrador", "Compras"}),
        ("Proveedores", "building", "proveedor_lista", {"Administrador", "Compras"}),
    ]),
    ("Sistema", [
        ("Actividad", "pulse", "actividad", {"Administrador"}),
        ("Ajustes", "settings", "ajustes_email", {"Administrador"}),
        ("Usuarios y roles", "users", "admin:index", {"Administrador"}),
    ]),
]


def rol_actual(user):
    if user.is_superuser:
        return "Administrador"
    for nombre in user.groups.values_list("name", flat=True):
        if nombre in ROL_DASHBOARD:
            return nombre
    return None


def navegacion(request):
    user = request.user
    if not user.is_authenticated:
        return {}
    rol = rol_actual(user)
    if rol is None:
        return {"rol_actual": None, "menu": []}

    actual = getattr(getattr(request, "resolver_match", None), "url_name", None)
    from . import services        # import tardío: services importa este módulo
    # Incidencias: el círculo muestra TODAS las abiertas; se resalta (rojo) solo si alguna espera tu acción
    accion_incidencias = services.contar_acciones_pendientes(rol) if rol in ROLES_INCIDENCIAS else 0
    contadores = {"incidencia_lista": services.contar_incidencias_abiertas() if rol in ROLES_INCIDENCIAS else 0}
    contadores["tarea_lista"] = services.contar_tareas_pendientes(rol)
    novedades = services.novedades_menu(user, rol)
    if rol == "Producción":
        contadores["notificacion_lista"] = services.contar_notificaciones_sin_leer(user)
    if rol == "Administrador":
        contadores["autorizacion_lista"] = services.contar_ordenes_por_autorizar()
    grupos = []
    for titulo, items in MENU:
        elementos = []
        for etiqueta, icono, nombre, roles in items:
            if rol not in roles:
                continue
            if nombre == "__dashboard__":
                nombre = ROL_DASHBOARD[rol]
            elementos.append({
                "etiqueta": etiqueta,
                "icono": icono,
                "url": reverse(nombre) if nombre else None,
                "activo": nombre is not None and actual is not None and nombre.split("_")[0] == actual.split("_")[0],
                "contador": contadores.get(nombre, 0),
                "novedad": novedades.get(nombre, False),
                "titulo_contador": (f"{contadores.get(nombre, 0)} abiertas · {accion_incidencias} esperan tu acción"
                                    if nombre == "incidencia_lista" else
                                    "Tareas pendientes de tu departamento" if nombre == "tarea_lista" else "Esperan tu acción"),
            })
        if elementos:
            grupos.append({"titulo": titulo, "items": elementos})

    nombre_completo = user.get_full_name() or user.get_username()
    iniciales = "".join(p[0] for p in nombre_completo.split()[:2]).upper() or "U"
    return {"rol_actual": rol, "menu": grupos, "nombre_usuario": nombre_completo, "iniciales": iniciales}
