from django import forms

import json

from .catalogo_incidencias import (ACCIONES_TAREA, RESOLUCIONES, acciones_de, genera_reposicion, presets_para,
                                   resoluciones_para)


def _area(placeholder, filas=3):
    return forms.Textarea(attrs={"class": "of-textarea", "rows": filas, "placeholder": placeholder})


class ComentarioForm(forms.Form):
    texto = forms.CharField(
        label="Mensaje", max_length=2000,
        widget=_area("Aporta información, pruebas o novedades sobre la incidencia…"),
        error_messages={"required": "Escribe un mensaje."},
    )


N_TAREAS = 4


class ResolucionForm(forms.Form):
    """Compras elige la resolución (según la categoría) y define hasta N_TAREAS tareas con su departamento."""
    # La elige la tarjeta pulsada (o «Personalizado»); `preset` recuerda cuál para volver a pintarla si hay errores
    resolucion = forms.ChoiceField(
        label="Resolución", widget=forms.HiddenInput,
        error_messages={"required": "Elige una solución recomendada o «Personalizado».",
                        "invalid_choice": "Elige una solución recomendada o «Personalizado»."},
    )
    preset = forms.CharField(required=False, max_length=30, widget=forms.HiddenInput)
    unidades_reposicion = forms.DecimalField(
        label="Unidades a reponer", required=False, min_value=0, max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "of-input", "step": "0.01", "inputmode": "decimal"}),
    )

    def __init__(self, incidencia, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.incidencia = incidencia
        self.fields["resolucion"].choices = [
            (k, RESOLUCIONES[k][0]) for k in resoluciones_para(incidencia.categoria, incidencia.subtipo)
        ]
        self.descripciones = {k: RESOLUCIONES[k][1] for k in RESOLUCIONES}
        self.presets = [dict(p, json=json.dumps({"resolucion": p["resolucion"], "tareas": p["tareas"]}, ensure_ascii=False))
                        for p in presets_para(incidencia)]
        self.repone = [k for k in RESOLUCIONES if genera_reposicion(k)]
        self.fields["unidades_reposicion"].initial = incidencia.unidades_afectadas or incidencia.linea.cantidad_pedida
        acciones = [("", "Elige la acción")] + [
            (dep, acciones_de(dep)) for dep in ("Almacén", "Compras", "Administrador", "Producción")
        ]
        departamentos = [("", "¿Quién?")] + [(d, d) for d in ("Almacén", "Compras", "Administrador", "Producción")]
        for n in range(N_TAREAS):
            self.fields[f"dep_{n}"] = forms.ChoiceField(required=False, choices=departamentos, widget=forms.Select(attrs={"class": "of-select", "data-dep": ""}))
            self.fields[f"acc_{n}"] = forms.ChoiceField(required=False, choices=acciones, widget=forms.Select(attrs={"class": "of-select", "data-acc": ""}))
            self.fields[f"txt_{n}"] = forms.CharField(required=False, max_length=1000, widget=_area("Información para quien la hace (opcional)", 2))

    def clean(self):
        datos = super().clean()
        for n in range(N_TAREAS):
            dep, acc, txt = datos.get(f"dep_{n}"), datos.get(f"acc_{n}"), (datos.get(f"txt_{n}") or "").strip()
            if not (dep or acc or txt):
                continue
            if not dep:
                self.add_error(f"dep_{n}", "Elige quién la hace.")
            elif not acc:
                self.add_error(f"acc_{n}", "Elige la acción.")
            elif dep not in ACCIONES_TAREA[acc][1]:
                self.add_error(f"acc_{n}", "Esa acción no la hace ese departamento.")
            elif acc == "otra" and not txt:
                self.add_error(f"txt_{n}", "Describe la acción.")
        return datos

    def filas_tareas(self):
        return [{"dep": self[f"dep_{n}"], "acc": self[f"acc_{n}"], "txt": self[f"txt_{n}"], "n": n}
                for n in range(N_TAREAS)]

    def tareas(self):
        return [{"departamento": self.cleaned_data[f"dep_{n}"], "accion": self.cleaned_data[f"acc_{n}"],
                 "texto": self.cleaned_data.get(f"txt_{n}", "")}
                for n in range(N_TAREAS) if self.cleaned_data.get(f"dep_{n}") and self.cleaned_data.get(f"acc_{n}")]


class AceptacionForm(forms.Form):
    """Comentario opcional al aceptar la medida o al cerrar la incidencia."""
    texto = forms.CharField(
        label="Comentario", required=False, max_length=2000,
        widget=_area("Comentario (opcional)", 2),
    )


class MotivoForm(forms.Form):
    texto = forms.CharField(
        label="Motivo", max_length=2000,
        widget=_area("Explica el motivo para que el responsable pueda actuar…"),
        error_messages={"required": "Indica el motivo."},
    )
