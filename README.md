# OrderFlow
 
Aplicación web de gestión de **órdenes de compra y recepción de almacén**, desarrollada como Proyecto Intermodular del Grado Superior en Desarrollo de Aplicaciones Multiplataforma (DAM).
 
Resuelve la falta de comunicación entre los departamentos de Compras y Almacén en pymes sin ERP: centraliza la creación, la autorización, la recepción y la gestión de incidencias de las órdenes de compra, con trazabilidad completa de cada cambio de estado.
 
## Funcionalidades
 
- Catálogo de proveedores y productos (el proveedor es fijo por producto).
- Creación de órdenes de compra por **Compras** y autorización por el **Administrador**, con envío automático del correo al proveedor.
- Verificación de la recepción línea a línea (OK / NG) por **Almacén**, pensada para móvil y tablet.
- Gestión de incidencias con fases, soluciones recomendadas, tareas por departamento, reposición automática y cierre de la orden.
- Aviso opcional a **Producción** cuando llega el material, con notificaciones individuales.
- Historial de estados y registro de actividad.
## Roles de usuario
 
| Rol | Qué hace |
| --- | --- |
| Compras | Crea órdenes y propone la resolución de las incidencias |
| Administrador | Autoriza órdenes, aprueba las medidas y cierra incidencias |
| Almacén | Verifica la recepción y abre incidencias |
| Producción | Recibe notificaciones de llegada |
 
## Tecnologías
 
Python 3.13 · Django 6.1 · PostgreSQL · Bootstrap 5.3 · HTML, CSS y JavaScript.
 
## Requisitos previos
 
- Python 3.13
- PostgreSQL (con un servidor en marcha)
- Git
## Instalación
 
1. **Clonar el repositorio**
```bash
git clone https://github.com/itsComerm/Proyecto-Final.git
cd Proyecto-Final
```
 
2. **Crear y activar el entorno virtual**
```bash
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux / macOS
```
 
3. **Instalar las dependencias**
```bash
pip install -r requirements.txt
```
 
4. **Crear la base de datos en PostgreSQL**
```sql
CREATE DATABASE orderflow;
```
 
5. **Configurar el archivo `.env`**
   Crea un archivo `.env` en la raíz del proyecto (junto a `manage.py`) con tus credenciales. Este archivo no se sube al repositorio.
```env
DB_NAME=orderflow
DB_USER=postgres
DB_PASSWORD=tu_contraseña
DB_HOST=localhost
DB_PORT=5432
 
# Correo (en desarrollo puede usarse la consola)
EMAIL_HOST=smtp.ejemplo.com
EMAIL_PORT=587
EMAIL_HOST_USER=usuario@ejemplo.com
EMAIL_HOST_PASSWORD=tu_contraseña_de_correo
```
 
6. **Aplicar las migraciones** (crea las tablas y carga las unidades de medida)
```bash
python manage.py migrate
```
 
7. **Crear un superusuario** (acceso al panel de administración de Django)
```bash
python manage.py createsuperuser
```
 
8. **Arrancar el servidor**
```bash
python manage.py runserver
```
 
   La aplicación queda disponible en http://127.0.0.1:8000/
 
## Usuarios de prueba por rol
 
Cada rol es un grupo de Django. Desde el panel de administración (`/admin/`) crea un usuario por rol y asígnalo a su grupo:
 
| Usuario de ejemplo | Grupo |
| --- | --- |
| compras | Compras |
| admin_oc | Administrador |
| almacen | Almacén |
| produccion | Producción |
 
> Al entrar, cada usuario ve el menú y el panel propios de su rol.
 
## Ejecutar las pruebas
 
El proyecto incluye pruebas automáticas (233 en la versión actual):
 
```bash
python manage.py test
```
 
Para ejecutar solo una clase de pruebas:
 
```bash
python manage.py test pedidos.tests.NombreDeLaClase
```
 
## Acceso desde otro equipo de la red local
 
```bash
python manage.py runserver 0.0.0.0:8000
```
 
Añade la IP del equipo a `ALLOWED_HOSTS` en `settings.py` y permite el puerto 8000 en el cortafuegos.
 
## Estructura del proyecto
 
```
Proyecto-Final/
├── manage.py
├── requirements.txt
├── .env                # credenciales (no se versiona)
└── pedidos/            # aplicación principal
    ├── models.py       # modelos de datos
    ├── services.py     # lógica de negocio
    ├── views_*.py      # vistas por módulo
    ├── forms_*.py      # formularios
    └── templates/      # plantillas HTML
```
 
## Autoría
 
Roger Comerma · Proyecto Final DAM · Tutor: Javier Navazo
 