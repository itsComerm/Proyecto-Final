from django.urls import reverse

ROL_DASHBOARD = {
    "Administrador": "dashboard_admin",
    "Compras": "dashboard_compras",
    "Almacén": "dashboard_almacen",
    "Producción": "dashboard_produccion",
}
TODOS = {"Administrador", "Compras", "Almacén", "Producción"}

# (etiqueta, icono, nombre de URL o None si aún no existe, roles que lo ven)
MENU = [
    ("Operativa", [
        ("Dashboard", "home", "__dashboard__", TODOS),
        ("Órdenes de compra", "doc", None, {"Administrador", "Compras", "Producción"}),
        ("Recepción", "inbox", None, {"Administrador", "Almacén"}),
        ("Incidencias", "alert", None, {"Administrador", "Compras", "Almacén"}),
    ]),
    ("Catálogo", [
        ("Productos", "box", None, {"Administrador", "Compras"}),
        ("Proveedores", "building", None, {"Administrador", "Compras"}),
    ]),
    ("Sistema", [
        ("Actividad", "pulse", None, {"Administrador"}),
        ("Usuarios y roles", "users", None, {"Administrador"}),
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
                "activo": nombre is not None and nombre == actual,
            })
        if elementos:
            grupos.append({"titulo": titulo, "items": elementos})

    nombre_completo = user.get_full_name() or user.get_username()
    iniciales = "".join(p[0] for p in nombre_completo.split()[:2]).upper() or "U"
    return {"rol_actual": rol, "menu": grupos, "nombre_usuario": nombre_completo, "iniciales": iniciales}
