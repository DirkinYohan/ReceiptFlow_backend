# ReceiptFlow Backend

Backend profesional y escalable de **ReceiptFlow**, aplicación web para gestionar recibos y facturas mediante OCR con Google Cloud Vision API.

## Estructura del proyecto

```
ReceiptFlow_backend/
├── config/                    # Configuración global de Django
│   ├── settings/              # Settings por entorno (base, development, production)
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py                # URLs globales
│   ├── wsgi.py                # Entry point WSGI
│   └── asgi.py                # Entry point ASGI
│
├── apps/                      # Aplicaciones Django (dominio)
│   ├── users/                 # Autenticación y usuarios personalizados
│   ├── documents/             # Documentos (recibos/facturas) + archivos subidos
│   ├── expenses/              # Registro de gastos
│   ├── categories/            # Categorías de gastos
│   ├── suppliers/             # Proveedores/emisores
│   └── reports/               # Reportes y agrupaciones
│
├── services/                  # Servicios de infraestructura
│   ├── google_vision/         # Cliente de Google Cloud Vision API
│   ├── ocr/                   # Abstracción y registros de servicios OCR
│   └── document_processor/    # Orquestación OCR -> datos estructurados
│
├── requirements/              # Dependencias por entorno
│   ├── base.txt
│   ├── development.txt
│   └── production.txt
│
├── media/                     # Archivos subidos por los usuarios
├── static/                    # Archivos estáticos globales
├── .env                       # Variables de entorno (ignorado por git)
├── .env.example               # Plantilla de variables de entorno
├── .gitignore
├── manage.py
├── README.md
└── requirements.txt           # Apunta a requirements/development.txt
```

## Stack

- **Python 3.10+**
- **Django 4.2+**
- **Django REST Framework 3.14+**
- **PostgreSQL**
- **Google Cloud Vision API**
- **JWT** (djangorestframework-simplejwt)
- **CORS** (django-cors-headers)

## Cómo ejecutar el backend localmente

1. **Crear y activar entorno virtual**
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. **Instalar dependencias**
   ```powershell
   pip install -r requirements.txt
   ```

3. **Configurar variables de entorno**

   Copia `.env.example` a `.env` y completa los valores, especialmente los de PostgreSQL:
   ```
   DB_NAME=receiptflow
   DB_USER=postgres
   DB_PASSWORD=tu_password
   ```

4. **Crear base de datos en PostgreSQL**
   ```sql
   CREATE DATABASE receiptflow;
   ```

5. **Ejecutar migraciones**
   ```powershell
   python manage.py migrate
   ```

6. **Crear superusuario (opcional)**
   ```powershell
   python manage.py createsuperuser
   ```

7. **Levantar el servidor de desarrollo**
   ```powershell
   python manage.py runserver
   ```

   Backend disponible en `http://localhost:8000`
   Admin en `http://localhost:8000/admin/`

## Qué queda preparado para la siguiente etapa

- Arquitectura modular con `config/`, `apps/` y `services/` separados.
- Settings por entorno (`base`, `development`, `production`) con variables de entorno.
- PostgreSQL configurado como motor de base de datos por defecto.
- DRF + JWT + CORS listos para conectar con Next.js en `http://localhost:3000`.
- Modelos iniciales (User, Document, Expense, Category, Supplier, Report) con serializers, viewsets, urls y admin.
- Endpoints REST versionados bajo `/api/v1/`.
- Carpetas `media/` y `static/` configuradas.
- Abstracción de servicios para Google Cloud Vision (`services/google_vision/client.py`), OCR (`services/ocr/`) y procesamiento de documentos (`services/document_processor/processor.py`), con métodos preparados para implementar la lógica de extracción.
