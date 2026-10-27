import re
from html import escape

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin

from . import services
from .decorators import rol_requerido
from .forms_ajustes import PlantillaEmailForm
from .models import PlantillaEmail


@rol_requerido("Administrador")
def ajustes_email(request):
    plantilla = PlantillaEmail.obtener()
    if request.method == "POST":
        if request.POST.get("accion") == "restaurar":
            for campo, valor in PlantillaEmail.POR_DEFECTO.items():
                setattr(plantilla, campo, valor)
            plantilla.actualizado_por = request.user
            plantilla.save()
            messages.success(request, "Plantilla restaurada a los textos por defecto.")
            return redirect("ajustes_email")
        form = PlantillaEmailForm(request.POST, instance=plantilla)
        if form.is_valid():
            plantilla = form.save(commit=False)
            plantilla.actualizado_por = request.user
            plantilla.save()
            messages.success(request, "Plantilla del email guardada. Se usará en las próximas autorizaciones.")
            return redirect("ajustes_email")
    else:
        form = PlantillaEmailForm(instance=plantilla)
    return render(request, "pedidos/ajustes_email.html", {
        "titulo": "Ajustes", "form": form, "plantilla": plantilla,
        "con_errores": form.is_bound and bool(form.errors),
    })


@rol_requerido("Administrador")
@xframe_options_sameorigin
def ajustes_email_vista_previa(request):
    """HTML del email con datos de ejemplo. Admite los textos sin guardar por GET para la vista previa en vivo."""
    guardada = PlantillaEmail.obtener()
    borrador = PlantillaEmail(**{
        campo: (request.GET.get(campo) if campo in request.GET else getattr(guardada, campo))[:1000]
        for campo in PlantillaEmail.POR_DEFECTO
    })
    asunto, _, html = services.componer_email(services.DATOS_EJEMPLO, borrador)
    banda = ('<div style="font:13px Arial,sans-serif;padding:10px 16px;background:#ffffff;border-bottom:1px solid #e2e7ee;color:#1f2937">'
             '<b>Asunto:</b> ' + escape(asunto) + '</div>')
    html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + banda, html, count=1)
    return HttpResponse(html)
