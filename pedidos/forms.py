from django import forms

from .models import Producto, Proveedor


class EstiloOrderFlowMixin:
    """Aplica las clases del sistema de diseño a todos los campos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            clase = "of-select" if isinstance(campo.widget, forms.Select) else "of-input"
            campo.widget.attrs["class"] = clase


class ProveedorForm(EstiloOrderFlowMixin, forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = ["nombre", "cif", "email", "telefono", "direccion"]


class ProductoForm(EstiloOrderFlowMixin, forms.ModelForm):
    class Meta:
        model = Producto
        fields = ["nombre", "proveedor"]
