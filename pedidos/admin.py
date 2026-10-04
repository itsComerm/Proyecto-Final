from django.contrib import admin
from .models import UnidadMedida, Proveedor, Producto, OrdenCompra, LineaPedido, Incidencia, HistorialEstado

admin.site.register(UnidadMedida)
admin.site.register(Proveedor)
admin.site.register(Producto)
admin.site.register(OrdenCompra)
admin.site.register(LineaPedido)
admin.site.register(Incidencia)
admin.site.register(HistorialEstado)