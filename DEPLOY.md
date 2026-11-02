# Despliegue de ReceiptFlow (capa gratuita)

- **Backend (Django):** Render, plan *Free*.
- **Frontend (Next.js):** Vercel, plan *Hobby*.
- **Base de datos y archivos:** Supabase (la misma base que ya usas).

Orden: **1. Supabase → 2. Render → 3. Vercel → 4. Volver a Render (CORS) → 5. Cron diario.**

---

## 1. Supabase

### 1.1 Datos de conexión a la base
*Project → Connect* (botón arriba) → pestaña **Connection string** → método **Session pooler**.
Anota: `host` (p. ej. `aws-0-us-east-2.pooler.supabase.com`), `port` (`5432`),
`database` (`postgres`), `user` (`postgres.<project-ref>`) y la contraseña de la base.

> Usa el **Session pooler**, no la conexión directa: Render solo sale por IPv4 y la conexión
> directa de Supabase es IPv6.

### 1.2 Bucket para los recibos
1. *Storage → New bucket* → nombre **`receipts`** → **Public bucket: desactivado** → *Create*.
2. *Storage → Settings → S3 Connection* → activa **Enable connection via S3 protocol**.
   Anota el **Endpoint** (`https://<project-ref>.supabase.co/storage/v1/s3`) y la **Region**.
3. En la misma página, *S3 Access Keys → New access key* → anota **Access key ID** y
   **Secret access key** (el secreto solo se muestra una vez).

### 1.3 Subir los recibos que ya tienes en tu equipo
Los documentos actuales tienen el archivo en `media/` de tu PC. Súbelos una vez (en PowerShell,
dentro de `ReceiptFlow_backend`):

```powershell
$env:STORAGE_BACKEND="s3"; $env:S3_BUCKET_NAME="receipts"
$env:S3_ENDPOINT_URL="https://<project-ref>.supabase.co/storage/v1/s3"; $env:S3_REGION="us-east-2"
$env:S3_ACCESS_KEY_ID="<access key id>"; $env:S3_SECRET_ACCESS_KEY="<secret>"
.venv\Scripts\python.exe manage.py upload_media_to_storage
```

Se puede repetir sin problema: salta los que ya están en el bucket.

---

## 2. Render (backend)

El repositorio trae `render.yaml`, así que Render lo configura casi todo.

1. *Dashboard → New → **Blueprint***.
2. Conecta GitHub y elige el repositorio **ReceiptFlow_backend**, rama `main`.
3. *Blueprint Name:* `receiptflow`. Render lee `render.yaml` y te pide los valores marcados como
   secretos. Rellénalos así:

| Variable | Valor |
|---|---|
| `DB_HOST` | host del *Session pooler* (paso 1.1) |
| `DB_USER` | `postgres.<project-ref>` |
| `DB_PASSWORD` | contraseña de la base |
| `S3_ENDPOINT_URL` | Endpoint S3 (paso 1.2) |
| `S3_ACCESS_KEY_ID` | Access key ID (paso 1.2) |
| `S3_SECRET_ACCESS_KEY` | Secret access key (paso 1.2) |
| `GEMINI_API_KEY` | tu clave de Google AI Studio |
| `APP_FRONTEND_URL` | por ahora `https://example.com` (se cambia en el paso 4) |
| `CORS_ALLOWED_ORIGINS` | por ahora `https://example.com` (se cambia en el paso 4) |
| `EMAIL_HOST_USER` | tu correo de Gmail |
| `EMAIL_HOST_PASSWORD` | contraseña de aplicación de Gmail (16 caracteres) |
| `DEFAULT_FROM_EMAIL` | `ReceiptFlow <tu-correo@gmail.com>` |

`DJANGO_SECRET_KEY` y `CRON_SECRET` se generan solos. El resto ya viene con su valor.

4. *Apply*. El primer despliegue tarda unos minutos. Cuando termine, la URL será algo como
   `https://receiptflow-api.onrender.com`. Comprueba `https://<tu-url>/healthz/` → `{"status": "ok"}`.

### Si prefieres crear el servicio a mano (sin Blueprint)
*New → Web Service* → repositorio **ReceiptFlow_backend**:

| Campo | Valor |
|---|---|
| Name | `receiptflow-api` |
| Region | **Ohio (US East)** (la misma que Supabase `us-east-2`) |
| Branch | `main` |
| Root Directory | *(vacío)* |
| Runtime / Language | **Python 3** |
| Build Command | `pip install -r requirements/production.txt && python manage.py collectstatic --noinput` |
| Start Command | `python manage.py migrate --noinput && python manage.py seed_categories && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 180 --access-logfile -` |
| Instance Type | **Free** |
| Health Check Path (*Advanced*) | `/healthz/` |

Y en *Environment* agrega todas las variables de `render.yaml`, incluidas:
`DJANGO_SETTINGS_MODULE=config.settings.production`, `PYTHON_VERSION=3.13.5`,
`DJANGO_DEBUG=False`, `STORAGE_BACKEND=s3`, `S3_BUCKET_NAME=receipts`, `S3_REGION=us-east-2`,
`DB_PORT=5432`, `DB_NAME=postgres`, `DB_SSLMODE=require`, `DB_POOL_MIN_SIZE=2`, `DB_POOL_MAX_SIZE=4`,
`EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST=smtp.gmail.com`,
`EMAIL_PORT=587`, `EMAIL_USE_TLS=True`, `EMAIL_VERIFICATION_REQUIRED=True`,
`GEMINI_MODEL=gemini-3.5-flash`, `OCR_SERVICE=gemini`, y `DJANGO_SECRET_KEY` / `CRON_SECRET`
con valores largos y aleatorios (*Generate*).

---

## 3. Vercel (frontend)

1. *Add New → Project* → importa el repositorio **Receiptflow_frontend**.
2. Campos:

| Campo | Valor |
|---|---|
| Framework Preset | **Next.js** (se detecta solo) |
| Root Directory | `./` |
| Build Command | *(por defecto)* `next build` |
| Output Directory | *(por defecto)* |
| Install Command | *(por defecto)* |
| Environment Variable | `NEXT_PUBLIC_API_URL` = `https://<tu-url-de-render>` **sin barra al final** |

3. *Deploy*. Anota la URL de producción (p. ej. `https://receiptflow.vercel.app`).

> `NEXT_PUBLIC_API_URL` se incrusta al compilar: si la cambias, vuelve a desplegar (*Redeploy*).

---

## 4. Conectar frontend y backend

En Render → servicio → *Environment*, cambia:

| Variable | Valor |
|---|---|
| `APP_FRONTEND_URL` | `https://receiptflow.vercel.app` (tu URL de Vercel) |
| `CORS_ALLOWED_ORIGINS` | `https://receiptflow.vercel.app` |
| `CORS_ALLOWED_ORIGIN_REGEXES` *(opcional, para las vistas previas de Vercel)* | `^https://receiptflow-[a-z0-9-]+\.vercel\.app$` |

Guarda (*Save, rebuild and deploy*). Sin barra al final en las URLs.

---

## 5. Tareas diarias (gratis, con cron-job.org)

El plan gratuito de Render no tiene workers ni cron, así que un servicio externo dispara las tareas
diarias (avisos de facturas por vencer y documentos atascados).

1. En Render → *Environment*, copia el valor de **`CRON_SECRET`**.
2. En <https://cron-job.org> → *Create cronjob*:
   - **URL:** `https://<tu-url-de-render>/api/internal/run-scheduled-tasks/`
   - **Schedule:** todos los días a las **08:00**, zona horaria **America/Bogota**.
   - *Advanced* → **Request method:** `POST`.
   - *Advanced* → **Headers:** `X-Cron-Secret` = el valor de `CRON_SECRET`.
   - *Advanced* → **Timeout:** el máximo (el servicio puede tardar en despertar).

Sin la cabecera correcta el endpoint responde 404.

---

## Cómo se comporta la capa gratuita

- **Se duerme tras 15 minutos sin uso.** La primera petición después tarda ~1 minuto
  (arranque + migraciones). Las siguientes van normal.
- **Lectura de documentos:** sin worker, el recibo se lee con Gemini dentro de la misma subida
  (10–30 s). El límite de tiempo de gunicorn es 180 s.
- **Archivos:** el disco de Render se borra en cada reinicio; por eso los recibos van a Supabase
  Storage con enlaces firmados que caducan en 1 hora.
- **Correo:** si los correos de verificación no llegan, revisa los *Logs* de Render. Si el puerto
  587 está bloqueado, usa un proveedor SMTP con puerto 2525 (p. ej. Brevo) o pon temporalmente
  `EMAIL_VERIFICATION_REQUIRED=False` para que el registro no dependa del correo.
- **Gemini gratis:** ~20 lecturas al día por modelo; después usa el modelo de respaldo.
- **Conexiones a Supabase:** 2 workers × 4 conexiones = 8 como máximo. Si también corres el
  backend en tu PC contra la misma base, baja allí `DB_POOL_MIN_SIZE`.

## Variables de entorno (resumen)

Todas están documentadas en `.env.example`.
