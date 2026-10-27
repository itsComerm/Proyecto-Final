from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import LineaPedido, OrdenCompra, Producto, Proveedor, UnidadMedida


class ProductoSelect(forms.Select):
    """Select que añade data-proveedor a cada opción (el JavaScript filtra por él)."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opcion = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instancia = getattr(value, "instance", None)
        if instancia is not None:
            opcion["attrs"]["data-proveedor"] = instancia.proveedor_id
        return opcion


class ProductoChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.nombre          # el proveedor ya está elegido arriba


class OrdenForm(forms.ModelForm):
    proveedor = forms.ModelChoiceField(
        queryset=Proveedor.objects.order_by("nombre"),
        empty_label="Selecciona un proveedor",
        label="Proveedor",
        error_messages={"required": "Selecciona un proveedor."},
    )
    field_order = ["proveedor", "notificar_produccion"]

    class Meta:
        model = OrdenCompra
        fields = ["notificar_produccion"]
        labels = {"notificar_produccion": "Avisar a Producción"}
        widgets = {"notificar_produccion": forms.CheckboxInput(attrs={"class": "of-switch", "role": "switch"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proveedor"].widget.attrs["class"] = "of-select"


class LineaForm(forms.ModelForm):
    producto = ProductoChoiceField(
        queryset=Producto.objects.select_related("proveedor").order_by("nombre"),
        widget=ProductoSelect,
        empty_label="Producto",
        error_messages={"required": "Elige un producto."},
    )
    unidad_medida = forms.ModelChoiceField(
        queryset=UnidadMedida.objects.order_by("id"),
        empty_label="Unidad",
        error_messages={"required": "Elige una unidad."},
    )
    cantidad_pedida = forms.DecimalField(
        min_value=Decimal("0.01"), max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01", "placeholder": "Cantidad"}),
        error_messages={"required": "Indica la cantidad."},
    )

    class Meta:
        model = LineaPedido
        fields = ["producto", "unidad_medida", "cantidad_pedida"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].widget.attrs.update({"class": "of-select", "aria-label": "Producto"})
        self.fields["unidad_medida"].widget.attrs.update({"class": "of-select", "aria-label": "Unidad de medida"})
        self.fields["cantidad_pedida"].widget.attrs.update({"class": "of-input", "aria-label": "Cantidad"})


class BaseLineaFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        vistos = set()
        lineas = 0
        for form in self.forms:
            if self._should_delete_form(form) or not form.cleaned_data:
                continue
            producto = form.cleaned_data.get("producto")
            if producto is None:
                continue
            lineas += 1
            if producto.pk in vistos:
                form.add_error("producto", "Este producto ya está en la orden.")
            vistos.add(producto.pk)
        if lineas == 0:
            raise forms.ValidationError("Añade al menos una línea a la orden.")


LineaFormSet = inlineformset_factory(
    OrdenCompra, LineaPedido,
    form=LineaForm, formset=BaseLineaFormSet,
    extra=0, min_num=1, can_delete=True,
)
