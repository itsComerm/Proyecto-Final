from django import forms

from .catalogo_incidencias import SUBTIPOS
from .models import Incidencia


SUBTIPOS_JSON = {k: [list(x) for x in v] for k, v in SUBTIPOS.items() if k != "cantidad"}


class RecepcionForm(forms.Form):
    """Un OK/NG por línea. Los campos se crean dinámicamente: res_<id>, desc_<id>, cant_<id>."""

    def __init__(self, lineas, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lineas = lineas
        for l in lineas:
            self.fields[f"res_{l.pk}"] = forms.ChoiceField(
                choices=[("ok", "OK"), ("ng", "NG")],
                error_messages={"required": "Marca OK o NG.", "invalid_choice": "Marca OK o NG."},
            )
            self.fields[f"cat_{l.pk}"] = forms.ChoiceField(
                required=False, choices=[("", "Elige la categoría")] + list(Incidencia.CATEGORIAS),
                widget=forms.Select(attrs={"class": "of-select", "data-cat": ""}),
            )
            self.fields[f"sub_{l.pk}"] = forms.CharField(required=False, max_length=20, widget=forms.HiddenInput())
            self.fields[f"afec_{l.pk}"] = forms.DecimalField(
                required=False, min_value=0, max_digits=10, decimal_places=2,
                widget=forms.NumberInput(attrs={"class": "of-input", "step": "0.01", "inputmode": "decimal"}),
            )
            self.fields[f"desc_{l.pk}"] = forms.CharField(
                required=False, max_length=1000,
                widget=forms.Textarea(attrs={"class": "of-textarea", "rows": 3,
                                             "placeholder": "Información adicional sobre lo ocurrido"}),
            )
            self.fields[f"cant_{l.pk}"] = forms.DecimalField(
                required=False, min_value=0, max_digits=10, decimal_places=2,
                widget=forms.NumberInput(attrs={"class": "of-input", "step": "0.01", "inputmode": "decimal"}),
            )

    def clean(self):
        datos = super().clean()
        for l in self.lineas:
            if datos.get(f"res_{l.pk}") != "ng":
                continue
            cat = datos.get(f"cat_{l.pk}")
            sub = datos.get(f"sub_{l.pk}") or ""
            if not cat:
                self.add_error(f"cat_{l.pk}", "Elige la categoría de la incidencia.")
            elif cat == "cantidad":
                recibida = datos.get(f"cant_{l.pk}")
                if recibida is None:
                    self.add_error(f"cant_{l.pk}", "Indica cuántas unidades se han recibido.")
                elif recibida == l.cantidad_pedida:
                    self.add_error(f"cant_{l.pk}", "Coincide con lo pedido: no hay error de cantidad.")
            elif cat in SUBTIPOS:
                if sub not in dict(SUBTIPOS[cat]):
                    self.add_error(f"sub_{l.pk}", "Elige el tipo de problema.")
                if cat in ("producto", "danado") and not datos.get(f"afec_{l.pk}"):
                    self.add_error(f"afec_{l.pk}", "Indica las unidades afectadas.")
            elif cat == "otra" and not (datos.get(f"desc_{l.pk}") or "").strip():
                self.add_error(f"desc_{l.pk}", "Describe el problema.")
        return datos

    def filas(self):
        """Una fila por línea, con sus tres campos enlazados, lista para la plantilla."""
        return [
            {"linea": l, "res": self[f"res_{l.pk}"], "desc": self[f"desc_{l.pk}"], "cant": self[f"cant_{l.pk}"],
             "cat": self[f"cat_{l.pk}"], "sub": self[f"sub_{l.pk}"], "afec": self[f"afec_{l.pk}"],
             "subtipos": SUBTIPOS_JSON}
            for l in self.lineas
        ]

    def resultados(self):
        return {
            l.pk: {
                "resultado": self.cleaned_data[f"res_{l.pk}"],
                "descripcion": self.cleaned_data.get(f"desc_{l.pk}", ""),
                "categoria": self.cleaned_data.get(f"cat_{l.pk}", ""),
                "subtipo": self.cleaned_data.get(f"sub_{l.pk}", ""),
                "afectadas": self.cleaned_data.get(f"afec_{l.pk}"),
                "cantidad": self.cleaned_data.get(f"cant_{l.pk}"),
            }
            for l in self.lineas
        }
