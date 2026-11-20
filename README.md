# QuinuAlert API — recepción segura de mildiu

Backend FastAPI para registrar tomas semanales, surcos georreferenciados, resultados de IA e imágenes de hojas en PostgreSQL/PostGIS. Las rutas de prototipos usan HTTPS, HMAC-SHA256, timestamp y nonce persistente. El frontend React no forma parte de este proyecto.

## Decisiones de integración

Se inspeccionaron el backend existente, el catálogo PostgreSQL y [esquema_quinua_iot.sql](esquema_quinua_iot.sql) antes de diseñar la actualización. La tabla existente `terrenos` tiene `terreno_id BIGINT` como PK, `propietario_id BIGINT`, `descripcion TEXT`, `direccion TEXT`, `area DOUBLE PRECISION`, `poligono geometry(Polygon,4326)`, `activo BOOLEAN` y `actualizado_en TIMESTAMPTZ`; conserva sus restricciones e índices. La actualización es exclusivamente aditiva.

El servicio sigue la separación ya utilizada por QuinuAlert:

- `app/api/v1`: límite HTTP y parsing posterior a la autenticación.
- `app/security`: canonicalización HMAC, credenciales cifradas y nonces PostgreSQL.
- `app/schemas`: contratos estrictos Pydantic v2.
- `app/models`: modelos SQLAlchemy 2.x y geometrías GeoAlchemy/PostGIS.
- `app/services`: reglas transaccionales, idempotencia y archivos.
- `esquema_quinua_iot.sql`: copia intacta del esquema base que ya está en producción.
- `script.sql`: actualización complementaria, manual y transaccional del módulo mildiu.
- `scripts`: aprovisionamiento y cliente Raspberry de referencia.

Las claves de dispositivo se cifran con Fernet bajo `HMAC_MASTER_KEY`. Cada credencial tiene `key_id`, vigencia y estado propios, lo que permite rotación y revocación. La clave maestra debe residir en el gestor de secretos del despliegue, nunca en Git.

## Instalación

Requiere Python 3.13, PostgreSQL y PostGIS.

```powershell
py -3.13 -m venv .venv
py -3.13 -m pip --python .venv install -r requirements-dev.txt
Copy-Item .env.example .env
```

Genere la clave maestra:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Edite `.env`. Después haga una copia de seguridad y ejecute manualmente [script.sql](script.sql), primero sobre una copia aislada de la base. **No vuelva a ejecutar `esquema_quinua_iot.sql` sobre producción**: ese archivo es solamente la referencia del esquema ya instalado. Desde `psql`, ejecute únicamente:

```powershell
$env:PGPASSWORD = '<password-postgresql>'
psql -v ON_ERROR_STOP=1 -h localhost -U postgres -d db_proyecto_quinua -f .\script.sql
Remove-Item Env:PGPASSWORD
```

También puede abrir `script.sql` en Query Tool de pgAdmin, conectado a la base correcta, y ejecutarlo completo. El script:

- valida PostGIS y la estructura real de `public.terrenos`;
- aborta si detecta una instalación previa o parcial del módulo;
- crea las nueve tablas, restricciones e índices dentro de una transacción;
- no modifica `terrenos` ni inserta datos o credenciales.

La aplicación no llama `create_all()` ni crea tablas al iniciar. Una vez aplicado el SQL, inicie el servicio:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

## Aprovisionamiento y rotación

El script SQL no inserta prototipos ni datos de prueba. Entregue el secreto al dispositivo por un canal separado y ejecute:

```powershell
$env:DEVICE_HMAC_SECRET = '<secreto-aleatorio-de-al-menos-32-bytes>'
.\.venv\Scripts\python.exe scripts/manage_prototype.py create `
  --codigo PROT-PUEMAPE-001 --nombre "Raspberry Puémape 1" `
  --key-id key-001 --terreno-id 1
Remove-Item Env:DEVICE_HMAC_SECRET
```

Rotación y revocación:

```powershell
$env:DEVICE_HMAC_SECRET = '<nuevo-secreto>'
.\.venv\Scripts\python.exe scripts/manage_prototype.py rotate --codigo PROT-PUEMAPE-001 --key-id key-002
Remove-Item Env:DEVICE_HMAC_SECRET
.\.venv\Scripts\python.exe scripts/manage_prototype.py revoke --codigo PROT-PUEMAPE-001
```

El script no imprime ni persiste el secreto en texto plano. Rotar retira inmediatamente las credenciales activas anteriores. Para una ventana de solapamiento, cree primero una política operativa específica en vez de ampliar estados manualmente.

## Firma HMAC v1

Todas las rutas bajo `/api/v1/mildiu` requieren exactamente una instancia de cada cabecera:

```text
X-Device-ID: PROT-PUEMAPE-001
X-Key-ID: key-001
X-Timestamp: 1795082400
X-Nonce: 8c3bde5f79a04fd28a43dcb8e6219f70
X-Content-SHA256: <sha256 hexadecimal de los bytes transmitidos>
X-Signature: <hmac-sha256 hexadecimal>
```

El mensaje UTF-8 v1 contiene ocho líneas sin salto final:

```text
METHOD
CANONICAL_PATH
CANONICAL_QUERY
DEVICE_ID
KEY_ID
TIMESTAMP
NONCE
BODY_SHA256
```

La consulta canónica ordena pares por nombre/valor y aplica percent-encoding RFC 3986. Las rutas actuales no necesitan query string. El servidor calcula nuevamente el SHA-256 del cuerpo, verifica la firma con comparación constante y consume el nonce mediante `UNIQUE(prototipo_id, credencial_id, nonce)`. El timestamp se valida en UTC con ±300 segundos por defecto; sincronice Raspberry Pi y servidor mediante NTP. Los nonces expirados se eliminan periódicamente y durante la autenticación. Cada reintento HTTP usa timestamp, nonce y firma nuevos, aunque conserve el mismo `client_toma_id` o `client_lote_id`.

Para multipart hay que firmar el cuerpo terminado, incluida la frontera. El cliente de ejemplo construye un objeto `httpx.Request`, llama `request.read()` para materializar exactamente esos bytes, calcula el hash y envía la misma instancia. No reconstruya el multipart después de firmarlo.

## Endpoints

- `POST /api/v1/mildiu/tomas`: registra de 1 a 5 surcos, resultados y geometrías. Devuelve `201`; un replay idéntico devuelve `200`. Un mismo `client_toma_id` con otro contenido devuelve `409`.
- `POST /api/v1/mildiu/tomas/{toma_id}/surcos/{surco_id}/imagenes`: multipart con `metadata` JSON y una parte por `campo_archivo`. Permite lotes reanudables e idempotentes.
- `POST /api/v1/mildiu/tomas/{toma_id}/finalizar`: cuerpo vacío. Exige todas las imágenes procesadas y ninguna pendiente.
- `GET /api/v1/mildiu/tomas/{toma_id}`: detalle, coordenadas, resultados, totales e imágenes por categoría.
- `GET /api/v1/mildiu/tomas/{toma_id}/estado`: avance, faltantes y último error recuperable.

Las respuestas exitosas tienen `{"success":true,"data":...}`. Los errores tienen:

```json
{
  "success": false,
  "error": {
    "code": "idempotency_conflict",
    "message": "client_toma_id ya fue usado con contenido diferente"
  }
}
```

Los contratos y ejemplos completos están en [docs/api_mildiu.md](docs/api_mildiu.md).

## Almacenamiento y recuperación

Los archivos se validan por extensión y contenido real con Pillow; solo JPEG/PNG, sin imágenes vacías, bombas de descompresión ni nombres del cliente como ruta. El servidor calcula SHA-256 y genera nombres UUID. La lectura del cuerpo tiene un timeout configurable (`REQUEST_BODY_TIMEOUT_SECONDS`). Las rutas públicas nunca contienen la ruta absoluta.

```text
imagenes_mildiu/2026/11/19-11-2026/
  terreno_1/toma_01_<uuid>/surco01_<uuid>/
    img-hojas-con-mildiu/<uuid>.jpg
    img-hojas-sin-mildiu/<uuid>.jpg
    img-hojas-con-posible-mildiu/<uuid>.png
```

Cada imagen pasa por `.staging`. PostgreSQL registra primero el lote y la ruta final con estado `pendiente`; luego ocurre el movimiento atómico y una segunda transacción marca `disponible`. Si el proceso se corta, el reconciliador de arranque o un reintento del lote termina el movimiento. Si faltan tanto staging como destino, marca el archivo/lote fallido y lo expone en el estado de la toma. El directorio de datos no debe servirse como contenido estático ni montarse en Nginx.

El límite semanal se calcula en `CROP_TIMEZONE` (`America/Lima` por defecto) y se protege con un advisory lock transaccional por terreno/año/semana, además de una restricción única por número de toma. Así, solicitudes concurrentes no pueden superar el máximo.

## Cliente Raspberry de referencia

```powershell
$env:QUINUALERT_DEVICE_ID = 'PROT-PUEMAPE-001'
$env:QUINUALERT_KEY_ID = 'key-001'
$env:QUINUALERT_DEVICE_SECRET = '<secreto>'
$env:QUINUALERT_BASE_URL = 'https://api.ejemplo.pe'

.\.venv\Scripts\python.exe scripts/raspberry_client.py register examples/toma.json
.\.venv\Scripts\python.exe scripts/raspberry_client.py upload <toma_id> <surco_id> `
  --image con_mildiu=.\pruebas\mildiu.jpg `
  --image sin_mildiu=.\pruebas\sana.jpg `
  --image posible_mildiu=.\pruebas\posible.png
.\.venv\Scripts\python.exe scripts/raspberry_client.py status <toma_id>
.\.venv\Scripts\python.exe scripts/raspberry_client.py finish <toma_id>
```

Guarde el `client_lote_id` mostrado y páselo con `--lote-id` para reintentar exactamente el mismo conjunto. El cliente valida certificados TLS y no ofrece una opción para desactivarlos.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
```

Para las pruebas geoespaciales use exclusivamente una base aislada ya migrada:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://usuario:clave@localhost:5432/quinuaalert_test'
.\.venv\Scripts\python.exe -m pytest -q tests
Remove-Item Env:TEST_DATABASE_URL
```

La suite unitaria no sustituye PostgreSQL por SQLite. La prueba PostGIS se omite si no existe `TEST_DATABASE_URL`.

## Producción y HTTPS

Use `ENFORCE_HTTPS=true`, `DB_ECHO=false`, permisos mínimos para el usuario de PostgreSQL y un usuario de sistema sin privilegios. Uvicorn debe escuchar solo en loopback o en una red privada. Termine TLS en un proxy con certificado válido, límite de cuerpo/tiempo y rate limiting compartido. Configure `--proxy-headers --forwarded-allow-ips <IP_REAL_DEL_PROXY>` únicamente si el acceso directo a Uvicorn está bloqueado; nunca use `*`. Consulte [docs/deployment.md](docs/deployment.md).

