from django.db import migrations

UNIDADES = ["Unidades", "Cajas", "Palets", "Kg", "Metros"]

def crear_unidades(apps, schema_editor):
    UnidadMedida = apps.get_model("pedidos", "UnidadMedida")
    for nombre in UNIDADES:
        UnidadMedida.objects.get_or_create(nombre=nombre)

def eliminar_unidades(apps, schema_editor):
    UnidadMedida = apps.get_model("pedidos", "UnidadMedida")
    UnidadMedida.objects.filter(nombre__in=UNIDADES).delete()

class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(crear_unidades, eliminar_unidades),
    ]