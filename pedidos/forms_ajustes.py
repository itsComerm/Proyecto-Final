from django import forms

from . import services
from .models import PlantillaEmail

ETIQUETAS = {
    "asunto": "Asunto",
    "saludo": "Saludo",
    "introduccion": "Introducción",
    "cierre": "Cierre, despedida y agradecimiento",
    "firma": "Firma: cargo o departamento",
}
AYUDAS = {
    "asunto": "Una sola línea. Puedes usar {codigo} y {proveedor}.",
    "saludo": "Puedes usar {proveedor}.",
    "introduccion": "Texto antes de la tabla del pedido. Puedes usar {codigo} y {proveedor}.",
    "cierre": "Texto después de la tabla. Cada salto de línea se respeta.",
    "firma": "Cargo o departamento. El nombre del operario y la fecha los añade el sistema.",
}


class PlantillaEmailForm(forms.ModelForm):
    class Meta:
        model = PlantillaEmail
        fields = list(ETIQUETAS)
        labels = ETIQUETAS
        help_texts = AYUDAS
        widgets = {
            "asunto": forms.TextInput(attrs={"class": "of-input"}),
            "saludo": forms.TextInput(attrs={"class": "of-input"}),
            "introduccion": forms.Textarea(attrs={"class": "of-textarea", "rows": 3}),
            "cierre": forms.Textarea(attrs={"class": "of-textarea", "rows": 5}),
            "firma": forms.TextInput(attrs={"class": "of-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.strip = True
            campo.error_messages["required"] = "Este campo no puede quedar vacío."

    def _comprobar(self, nombre):
        valor = self.cleaned_data.get(nombre, "")
        malas = services.variables_desconocidas(valor)
        if malas:
            raise forms.ValidationError(
                "Variable no válida: " + ", ".join("{" + m + "}" for m in malas) +
                ". Solo se pueden usar {codigo} y {proveedor}.")
        return valor

    def clean_asunto(self):
        valor = self._comprobar("asunto")
        if "\n" in valor or "\r" in valor:
            raise forms.ValidationError("El asunto debe ser una sola línea.")
        return valor

    def clean_saludo(self): return self._comprobar("saludo")
    def clean_introduccion(self): return self._comprobar("introduccion")
    def clean_cierre(self): return self._comprobar("cierre")
    def clean_firma(self): return self._comprobar("firma")
