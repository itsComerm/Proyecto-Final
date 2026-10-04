# OrderFlow

Aplicación web de gestión de órdenes de compra y recepción de almacén, desarrollada como Proyecto Intermodular del Grado Superior en Desarrollo de Aplicaciones Multiplataforma (DAM).

OrderFlow resuelve la falta de comunicación entre los departamentos de Compras y Almacén en pymes sin un ERP, centralizando la creación, autorización, recepción y gestión de incidencias de las órdenes de compra, con trazabilidad completa de cada cambio de estado.

## Stack tecnológico

- Python + Django
- PostgreSQL
- Bootstrap

## Roles de usuario

- Compras
- Administración
- Almacén
- Producción

## Instalación

\`\`\`bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
\`\`\`

## Autor

Roger Comerma — Proyecto Final DAM, tutor Javier Navazo.