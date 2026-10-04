from django.db import models
from django.contrib.auth.models import User


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

    # Notificación de llegada: selección múltiple por departamento.
    # "No" se representa dejando los tres campos en False (regla de exclusividad en el formulario).
    notificar_produccion = models.BooleanField(default=True)
    notificar_compras = models.BooleanField(default=False)
    notificar_administracion = models.BooleanField(default=False)

    def __str__(self):
        return f"Orden #{self.pk} ({self.get_estado_display()})"


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
    creado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="incidencias_creadas"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    gestion_compras = models.TextField(
        blank=True, help_text="Resolución gestionada por Compras"
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