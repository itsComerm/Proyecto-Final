from django.db import models
from django.contrib.auth.models import User
from django.db.models.functions import Lower
from django.utils import timezone


class UnidadMedida(models.Model):
    """Catálogo fijo de unidades de medida: Unidades, Cajas, Palets, Kg, Metros."""
    nombre = models.CharField(max_length=20, unique=True)

    class Meta:
        verbose_name = "Unidad de medida"
        verbose_name_plural = "Unidades de medida"

    def __str__(self):
        return self.nombre


class Proveedor(models.Model):
    nombre = models.CharField(max_length=150)
    cif = models.CharField(max_length=20, blank=True)
    email = models.EmailField(help_text="Dirección a la que se envía la orden autorizada")
    telefono = models.CharField(max_length=20, blank=True)
    direccion = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    """El proveedor es fijo por producto: no se comparten productos entre proveedores."""
    nombre = models.CharField(max_length=150)
    proveedor = models.ForeignKey(
        Proveedor, on_delete=models.PROTECT, related_name="productos"
    )
    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("nombre"),
                "proveedor",
                name="producto_unico_por_proveedor",
                violation_error_message="Este proveedor ya tiene un producto con ese nombre.",
            )
        ]

    def __str__(self):
        return f"{self.nombre} ({self.proveedor})"


class OrdenCompra(models.Model):
    ESTADOS = [
        ("creada", "Creada"),
        ("en_camino", "En camino"),
        ("incidencia", "Incidencia"),
        ("incidencia_resuelta", "Incidencia resuelta"),
        ("recibido", "Recibido"),
    ]

    creado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="ordenes_creadas"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=25, choices=ESTADOS, default="creada")

    # Aviso a Producción (interruptor Sí/No, por defecto No): recibe notificaciones cuando la orden llega
    # (con o sin incidencia) y cuando se resuelve una incidencia.
    notificar_produccion = models.BooleanField(default=False)

    # Orden normal o de reposición generada por una incidencia (no envía email al proveedor)
    TIPOS = [("normal", "Normal"), ("incidencia", "Reposición por incidencia")]
    tipo = models.CharField(max_length=15, choices=TIPOS, default="normal")
    origen = models.ForeignKey(
        "Incidencia", on_delete=models.SET_NULL, null=True, blank=True, related_name="reposiciones",
        help_text="Incidencia que originó esta orden de reposición",
    )

    def __str__(self):
        return f"Orden #{self.pk} ({self.get_estado_display()})"
    @property
    def codigo(self):
        """Identificador legible: OC-2026-0007."""
        if self.pk is None:
            return "OC-nueva"
        return f"OC-{timezone.localtime(self.fecha_creacion).year}-{self.pk:04d}"

    @property
    def proveedor(self):
        """El proveedor se deduce de las líneas (todas son del mismo proveedor)."""
        for linea in self.lineas.all():
            return linea.producto.proveedor
        return None

    @property
    def departamentos_avisados(self):
        return ["Producción"] if self.notificar_produccion else []


class LineaPedido(models.Model):
    """La unidad de medida se fija por línea, no por producto, para permitir
    pedir el mismo producto en distintas unidades según el pedido."""
    orden = models.ForeignKey(
        OrdenCompra, on_delete=models.CASCADE, related_name="lineas"
    )
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    unidad_medida = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT)
    cantidad_pedida = models.DecimalField(max_digits=10, decimal_places=2)

    # Se rellenan al verificar la recepción en Almacén
    cantidad_verificada = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    RESULTADO = [("ok", "OK"), ("ng", "NG")]
    resultado_verificacion = models.CharField(
        max_length=10, choices=RESULTADO, null=True, blank=True
    )

    def __str__(self):
        return f"{self.producto} x {self.cantidad_pedida} {self.unidad_medida}"


class Incidencia(models.Model):
    linea = models.ForeignKey(
        LineaPedido, on_delete=models.CASCADE, related_name="incidencias"
    )
    descripcion = models.TextField(help_text="Descripción del operario de Almacén")
    # --- Clasificación hecha por Almacén al abrirla ---
    CATEGORIAS = [
        ("cantidad", "Cantidad incorrecta"),
        ("producto", "Producto equivocado"),
        ("danado", "Dañado / no conforme"),
        ("documentacion", "Falta documentación"),
        ("otra", "Otra"),
    ]
    categoria = models.CharField(max_length=15, choices=CATEGORIAS, default="otra")
    subtipo = models.CharField(max_length=20, blank=True, help_text="Tipo concreto dentro de la categoría")
    unidades_afectadas = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # --- Resolución elegida por Compras ---
    resolucion = models.CharField(max_length=20, blank=True)
    unidades_reposicion = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    creado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="incidencias_creadas"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    gestion_compras = models.TextField(
        blank=True, help_text="Resolución gestionada por Compras"
    )
    gestionada_por = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incidencias_gestionadas",
    )
    fecha_gestion = models.DateTimeField(null=True, blank=True)
    # --- Ticket: fases del flujo y responsable de ejecutar la medida ---
    FASES = [
        ("abierta", "Abierta"),
        ("propuesta", "Solución propuesta"),
        ("aceptada", "Tareas abiertas"),
        ("reposicion", "Esperando reposición"),
        ("solucionada", "Gestionada"),
        ("resuelta", "Cerrada"),
    ]
    fase = models.CharField(max_length=15, choices=FASES, default="abierta")
    responsable = models.CharField(
        max_length=20, blank=True, help_text="Rol que ejecuta la medida aceptada"
    )
    resuelta = models.BooleanField(default=False)
    resuelta_por = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incidencias_resueltas",
    )
    fecha_resolucion = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Incidencia en {self.linea}"


class MensajeIncidencia(models.Model):
    """Hilo del ticket: comentarios de los usuarios y eventos del flujo, en orden cronológico."""
    TIPOS = [
        ("apertura", "Incidencia abierta"),
        ("comentario", "Comentario"),
        ("propuesta", "Solución propuesta"),
        ("aceptacion", "Medida aceptada"),
        ("rechazo", "Propuesta rechazada"),
        ("ejecucion", "Gestión realizada"),
        ("devolucion", "Devuelta a ejecución"),
        ("cierre", "Incidencia cerrada"),
        ("tarea", "Tarea"),
    ]
    incidencia = models.ForeignKey(
        Incidencia, on_delete=models.CASCADE, related_name="mensajes"
    )
    autor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="mensajes_incidencia")
    rol = models.CharField(max_length=20, blank=True, help_text="Rol del autor en el momento de escribir")
    tipo = models.CharField(max_length=15, choices=TIPOS, default="comentario")
    texto = models.TextField(blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha", "pk"]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.incidencia_id}"


class HistorialEstado(models.Model):
    """Traza quién y cuándo cambió el estado de cada orden."""
    orden = models.ForeignKey(
        OrdenCompra, on_delete=models.CASCADE, related_name="historial"
    )
    estado_anterior = models.CharField(max_length=25, blank=True)
    estado_nuevo = models.CharField(max_length=25)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha"]

    def __str__(self):
        return f"{self.orden} : {self.estado_anterior} -> {self.estado_nuevo}"

class Notificacion(models.Model):
    """Aviso para un usuario concreto (hoy: operarios de Producción). Cada uno marca los suyos como leídos."""
    TIPOS = [
        ("recibida", "Orden recibida"),
        ("con_incidencia", "Orden recibida con incidencia"),
        ("incidencia_resuelta", "Incidencia resuelta"),
    ]
    destinatario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notificaciones")
    tipo = models.CharField(max_length=20, choices=TIPOS)
    orden = models.ForeignKey(OrdenCompra, on_delete=models.CASCADE, related_name="notificaciones")
    incidencia = models.ForeignKey(Incidencia, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    fecha = models.DateTimeField(auto_now_add=True)
    leida = models.BooleanField(default=False)

    class Meta:
        ordering = ["-fecha", "-pk"]
        indexes = [models.Index(fields=["destinatario", "leida"])]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.orden_id} → {self.destinatario}"

class PlantillaEmail(models.Model):
    """Estructura del email que recibe el proveedor. Hay un único registro (pk=1) que edita el Administrador.

    El detalle del pedido (tabla), la firma con el nombre del operario de Compras y la fecha los pone el
    sistema; aquí se editan los textos fijos. En los campos se pueden usar {codigo} y {proveedor}.
    """
    POR_DEFECTO = {
        "asunto": "Orden de compra {codigo}",
        "saludo": "Estimados señores de {proveedor}:",
        "introduccion": "Les remitimos la siguiente orden de compra {codigo} para su gestión y suministro.",
        "cierre": (
            "Les agradeceríamos que confirmaran la recepción de este pedido y nos indicaran la fecha prevista de entrega.\n"
            "Quedamos a su disposición para cualquier consulta. Muchas gracias por su colaboración.\n"
            "Un cordial saludo,"
        ),
        "firma": "Departamento de Compras",
    }
    asunto = models.CharField(max_length=200, default=POR_DEFECTO["asunto"])
    saludo = models.CharField(max_length=200, default=POR_DEFECTO["saludo"])
    introduccion = models.TextField(max_length=1000, default=POR_DEFECTO["introduccion"])
    cierre = models.TextField(max_length=1000, default=POR_DEFECTO["cierre"])
    firma = models.CharField(max_length=200, default=POR_DEFECTO["firma"],
                             help_text="Se muestra bajo el nombre del operario de Compras")
    actualizado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Plantilla de email"
        verbose_name_plural = "Plantilla de email"

    def __str__(self):
        return "Plantilla del email al proveedor"

    @classmethod
    def obtener(cls):
        return cls.objects.get_or_create(pk=1)[0]

class LecturaIncidencia(models.Model):
    """Hasta qué mensaje del hilo ha leído cada usuario (para el aviso «Hay actualizaciones»)."""
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    incidencia = models.ForeignKey(Incidencia, on_delete=models.CASCADE, related_name="lecturas")
    ultimo_mensaje = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["usuario", "incidencia"], name="lectura_unica_por_usuario"),
        ]

class Tarea(models.Model):
    """Gestión concreta que un departamento debe hacer para resolver una incidencia."""
    ESTADOS = [("borrador", "Pendiente de aprobar"), ("pendiente", "Pendiente"), ("completada", "Completada")]
    incidencia = models.ForeignKey(Incidencia, on_delete=models.CASCADE, related_name="tareas")
    departamento = models.CharField(max_length=20)
    accion = models.CharField(max_length=30)
    texto = models.TextField(blank=True)
    estado = models.CharField(max_length=12, choices=ESTADOS, default="borrador")
    # Tarea del sistema: se completa sola (p. ej. «Recepcionar la reposición» al verificar la orden)
    automatica = models.BooleanField(default=False)
    orden_reposicion = models.ForeignKey(
        OrdenCompra, on_delete=models.SET_NULL, null=True, blank=True, related_name="tareas_reposicion"
    )
    creada_por = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    visible_desde = models.DateTimeField(null=True, blank=True, help_text="Cuando el departamento la ve por primera vez")
    completada_por = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    fecha_completada = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.accion} · {self.departamento} ({self.estado})"


class VisitaSeccion(models.Model):
    """Última vez que el usuario abrió una sección (Incidencias, Tareas): sirve para el aviso de novedades del menú."""
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    seccion = models.CharField(max_length=20)
    ultima_visita = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["usuario", "seccion"], name="visita_unica_usuario_seccion")]