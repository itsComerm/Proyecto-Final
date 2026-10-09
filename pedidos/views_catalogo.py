from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count, ProtectedError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .decorators import rol_requerido
from .forms import ProductoForm, ProveedorForm
from .models import Producto, Proveedor


class CatalogoMixin:
    """Solo Administrador y Compras gestionan el catálogo."""

    roles = ("Administrador", "Compras")
    titulo = ""            # título de la pantalla
    url_lista = ""         # nombre de la URL del listado (para Cancelar / volver)

    def dispatch(self, request, *args, **kwargs):
        return rol_requerido(*self.roles)(super().dispatch)(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = self.titulo
        contexto["url_lista"] = reverse_lazy(self.url_lista)
        return contexto


class EliminarMixin:
    """Borrado con aviso claro si el registro está en uso (relaciones PROTECT)."""

    mensaje_ok = ""
    mensaje_en_uso = ""

    def form_valid(self, form):
        try:
            respuesta = super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, self.mensaje_en_uso)
            return redirect(self.url_lista)
        messages.success(self.request, self.mensaje_ok)
        return respuesta


# ---------- Proveedores ----------
class ProveedorLista(CatalogoMixin, ListView):
    model = Proveedor
    template_name = "pedidos/catalogo/proveedor_lista.html"
    context_object_name = "proveedores"
    titulo = "Proveedores"
    url_lista = "proveedor_lista"

    def get_queryset(self):
        return Proveedor.objects.annotate(num_productos=Count("productos")).order_by("nombre")


class ProveedorNuevo(CatalogoMixin, SuccessMessageMixin, CreateView):
    model = Proveedor
    form_class = ProveedorForm
    template_name = "pedidos/catalogo/form.html"
    success_url = reverse_lazy("proveedor_lista")
    success_message = "Proveedor creado correctamente."
    titulo = "Nuevo proveedor"
    url_lista = "proveedor_lista"


class ProveedorEditar(CatalogoMixin, SuccessMessageMixin, UpdateView):
    model = Proveedor
    form_class = ProveedorForm
    template_name = "pedidos/catalogo/form.html"
    success_url = reverse_lazy("proveedor_lista")
    success_message = "Proveedor actualizado correctamente."
    titulo = "Editar proveedor"
    url_lista = "proveedor_lista"


class ProveedorEliminar(CatalogoMixin, EliminarMixin, DeleteView):
    model = Proveedor
    template_name = "pedidos/catalogo/confirmar_borrado.html"
    success_url = reverse_lazy("proveedor_lista")
    titulo = "Eliminar proveedor"
    url_lista = "proveedor_lista"
    mensaje_ok = "Proveedor eliminado."
    mensaje_en_uso = "No se puede eliminar: el proveedor tiene productos asociados."


# ---------- Productos ----------
class ProductoLista(CatalogoMixin, ListView):
    model = Producto
    template_name = "pedidos/catalogo/producto_lista.html"
    context_object_name = "productos"
    titulo = "Productos"
    url_lista = "producto_lista"

    def get_template_names(self):
        # Con ?parcial=1 solo devolvemos el bloque de resultados (lo usa el JavaScript)
        if self.request.GET.get("parcial"):
            return ["pedidos/catalogo/_producto_resultados.html"]
        return [self.template_name]

    def get_queryset(self):
        consulta = Producto.objects.select_related("proveedor").order_by("nombre")
        texto = self.request.GET.get("q", "").strip()
        proveedor = self.request.GET.get("proveedor", "")
        if texto:
            consulta = consulta.filter(nombre__unaccent__icontains=texto)
        if proveedor.isdigit():
            consulta = consulta.filter(proveedor_id=proveedor)
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["lista_proveedores"] = Proveedor.objects.order_by("nombre")
        contexto["q"] = self.request.GET.get("q", "").strip()
        contexto["proveedor_sel"] = self.request.GET.get("proveedor", "")
        contexto["hay_filtros"] = bool(contexto["q"] or contexto["proveedor_sel"])
        return contexto


class ProductoNuevo(CatalogoMixin, SuccessMessageMixin, CreateView):
    model = Producto
    form_class = ProductoForm
    template_name = "pedidos/catalogo/form.html"
    success_url = reverse_lazy("producto_lista")
    success_message = "Producto creado correctamente."
    titulo = "Nuevo producto"
    url_lista = "producto_lista"


class ProductoEditar(CatalogoMixin, SuccessMessageMixin, UpdateView):
    model = Producto
    form_class = ProductoForm
    template_name = "pedidos/catalogo/form.html"
    success_url = reverse_lazy("producto_lista")
    success_message = "Producto actualizado correctamente."
    titulo = "Editar producto"
    url_lista = "producto_lista"


class ProductoEliminar(CatalogoMixin, EliminarMixin, DeleteView):
    model = Producto
    template_name = "pedidos/catalogo/confirmar_borrado.html"
    success_url = reverse_lazy("producto_lista")
    titulo = "Eliminar producto"
    url_lista = "producto_lista"
    mensaje_ok = "Producto eliminado."
    mensaje_en_uso = "No se puede eliminar: el producto aparece en órdenes de compra."
