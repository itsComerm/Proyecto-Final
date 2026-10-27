"""Catálogo de categorías, resoluciones y acciones de tarea del flujo de incidencias."""

SUBTIPOS = {
    "cantidad": [("faltan", "Faltan unidades"), ("sobran", "Sobran unidades")],
    "producto": [("color", "Color"), ("distinto", "Producto distinto"), ("formato", "Formato / envase"), ("otro", "Otro")],
    "danado": [("envase", "Envase roto"), ("humedad", "Humedad"), ("aspecto", "Aspecto"), ("caducidad", "Caducidad / lote")],
    "documentacion": [("albaran", "Albarán"), ("certificado", "Certificado"), ("otro", "Otro")],
}
CATEGORIAS_CON_UNIDADES = ("producto", "danado")

# clave -> (etiqueta, descripción, genera orden de reposición)
RESOLUCIONES = {
    "reponer": ("Reponer las unidades", "Se crea una orden de reposición", True),
    "devolver_reponer": ("Devolver y reponer", "Devolución más orden de reposición", True),
    "abono": ("Abono / nota de crédito", "Sin nuevo envío", False),
    "devolver_abono": ("Devolver y pedir abono", "El material vuelve al proveedor", False),
    "aceptar": ("Aceptar el material", "Se da por bueno tal cual", False),
    "devolver": ("Devolver el material", "Vuelve al proveedor", False),
    "reclamar_doc": ("Reclamar la documentación", "Al proveedor", False),
    "otra": ("Personalizado", "Flujo de tareas creado a mano", False),
    "otra_reponer": ("Personalizado", "Flujo a mano con orden de reposición", True),
}
_RES_POR_CATEGORIA = {
    "cantidad": {"faltan": ["reponer", "abono", "aceptar", "otra"], "sobran": ["aceptar", "devolver", "devolver_abono", "otra"]},
    "producto": ["devolver_reponer", "devolver_abono", "aceptar", "otra"],
    "danado": ["devolver_reponer", "reponer", "devolver_abono", "abono", "aceptar", "otra"],
    "documentacion": ["reclamar_doc", "aceptar", "otra"],
    "otra": ["otra", "aceptar", "reponer", "abono"],
}


def resoluciones_para(categoria, subtipo=""):
    opciones = _RES_POR_CATEGORIA.get(categoria, ["otra"])
    if isinstance(opciones, dict):
        opciones = opciones.get(subtipo, opciones["faltan"])
    # «Personalizado» (con o sin orden de reposición) está siempre disponible
    return list(opciones) + [k for k in ("otra", "otra_reponer") if k not in opciones]


def genera_reposicion(resolucion):
    return RESOLUCIONES.get(resolucion, ("", "", False))[2]


# clave -> (etiqueta, departamentos que pueden hacerla)
TODOS = ("Almacén", "Compras", "Administrador", "Producción")
ACCIONES_TAREA = {
    "preparar_devolucion": ("Preparar la devolución", ("Almacén",)),
    "recontar": ("Recontar o reetiquetar", ("Almacén",)),
    "cuarentena": ("Apartar el material en cuarentena", ("Almacén",)),
    "ubicar_mercancia": ("Ubicar en el almacén la mercancía aceptada", ("Almacén",)),
    "recepcionar_reposicion": ("Recepcionar la reposición", ("Almacén",)),   # solo la crea el sistema
    "avisar_reponer": ("Avisar al proveedor para reponer", ("Compras",)),
    "solicitar_abono": ("Solicitar abono o nota de crédito", ("Compras",)),
    "reclamar_documentacion": ("Reclamar documentación", ("Compras",)),
    "acordar_recogida": ("Acordar la recogida con el proveedor", ("Compras",)),
    "regularizar_factura": ("Avisar al proveedor para regularizar la factura", ("Compras",)),
    "aceptar_unidades": ("Aceptar unidades o material", ("Administrador",)),
    "autorizar_descuento": ("Autorizar un descuento", ("Administrador",)),
    "confirmar_valido": ("Confirmar si el material es válido", ("Producción",)),
    "comunicar_retraso": ("Comunicar el retraso", ("Producción",)),
    "otra": ("Otra acción (texto libre)", TODOS),
}
ACCIONES_SISTEMA = {"recepcionar_reposicion"}


def acciones_de(departamento):
    return [(k, v[0]) for k, v in ACCIONES_TAREA.items() if departamento in v[1] and k not in ACCIONES_SISTEMA]


def etiqueta_accion(clave):
    return ACCIONES_TAREA.get(clave, (clave,))[0]


# ---------------------------------------------------------------------------
# Soluciones recomendadas: tarjetas que rellenan la resolución y las tareas (todo sigue siendo editable)
# ---------------------------------------------------------------------------
def _n(valor):
    texto = f"{valor:f}"
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


def presets_para(incidencia):
    """Lista de tarjetas [{clave, titulo, descripcion, resolucion, repone, tareas:[{dep, acc, etiqueta, texto}]}]."""
    unidad = incidencia.linea.unidad_medida.nombre
    n = incidencia.unidades_afectadas
    cantidad = f"{_n(n)} {unidad}" if n else "las unidades afectadas"

    def t(dep, acc, texto=""):
        return {"dep": dep, "acc": acc, "etiqueta": etiqueta_accion(acc), "texto": texto}

    reponer = lambda txt: t("Compras", "avisar_reponer", txt)
    devolver = t("Almacén", "preparar_devolucion", "Material en la zona de incidencias, listo para recoger.")
    ubicar = t("Almacén", "ubicar_mercancia", "Ubicar en su sitio y marcar como completada.")
    categoria, sub = incidencia.categoria, incidencia.subtipo
    tarjetas = []
    if categoria == "cantidad" and sub == "faltan":
        tarjetas = [
            ("reponer", "Reponer lo que falta", "Compras avisa al proveedor y Almacén recibe la reposición.", "reponer",
             [reponer(f"Reponer {cantidad}.")]),
            ("abono", "Abono", "Compras reclama el abono de lo no entregado.", "abono",
             [t("Compras", "solicitar_abono", f"Abono de {cantidad} no entregadas.")]),
        ]
    elif categoria == "cantidad" and sub == "sobran":
        tarjetas = [
            ("aceptar", "Aceptar y quedarse el exceso", "Almacén ubica la mercancía.", "aceptar", [ubicar]),
            ("devolver", "Devolver el exceso", "Almacén prepara la devolución y Compras acuerda la recogida.", "devolver",
             [devolver, t("Compras", "acordar_recogida", f"Recoger {cantidad} de más.")]),
            ("regularizar", "Aceptar y regularizar la factura", "Almacén ubica y Compras avisa del ajuste.", "aceptar",
             [ubicar, t("Compras", "regularizar_factura", f"Se han recibido {cantidad} de más.")]),
        ]
    elif categoria in ("producto", "danado"):
        tarjetas = [
            ("devolver_reponer", "Devolver y reponer", "Almacén devuelve; Compras avisa y Almacén recibe la reposición.",
             "devolver_reponer", [devolver, reponer(f"Reponer {cantidad}.")]),
            ("devolver_abono", "Devolver y abono", "Almacén devuelve y Compras reclama el abono.", "devolver_abono",
             [devolver, t("Compras", "solicitar_abono", f"Abono de {cantidad}.")]),
            ("aceptar", "Aceptar el producto" if categoria == "producto" else "Aceptar el producto dañado",
             "Almacén ubica la mercancía.", "aceptar", [ubicar]),
        ]
    elif categoria == "documentacion":
        tarjetas = [("reclamar", "Reclamar la documentación", "Compras la pide al proveedor.", "reclamar_doc",
                     [t("Compras", "reclamar_documentacion", "Pedir la documentación que falta.")])]
    return [{"clave": c, "titulo": ti, "descripcion": d, "resolucion": r, "repone": genera_reposicion(r), "tareas": ta}
            for c, ti, d, r, ta in tarjetas]
