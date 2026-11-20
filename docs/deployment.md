# Despliegue HTTPS

## Topología

Internet → Nginx/HAProxy con TLS → Uvicorn privado → PostgreSQL/PostGIS. El volumen de imágenes solo se monta en el proceso API y en la copia de seguridad; Nginx no debe exponerlo.

`esquema_quinua_iot.sql` es el esquema base que ya está en producción: no lo modifique ni vuelva a ejecutarlo. Ejecute manualmente solo `script.sql` una vez, después de realizar una copia de seguridad y antes de iniciar los workers. El script complementario aborta y revierte la transacción si el esquema existente no coincide con lo esperado. Monte `IMAGE_STORAGE_ROOT` y su subdirectorio de staging en el mismo sistema de archivos para conservar el movimiento atómico.

```sh
psql -v ON_ERROR_STOP=1 -h servidor-db -U usuario -d db_proyecto_quinua -f script.sql
uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  --proxy-headers --forwarded-allow-ips 127.0.0.1
```

Ejemplo Nginx resumido:

```nginx
limit_req_zone $binary_remote_addr zone=mildiu:10m rate=60r/m;

server {
    listen 443 ssl http2;
    server_name api.ejemplo.pe;
    ssl_certificate /etc/letsencrypt/live/api.ejemplo.pe/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.ejemplo.pe/privkey.pem;

    client_max_body_size 55m;
    client_body_timeout 60s;

    location /api/v1/mildiu/ {
        limit_req zone=mildiu burst=20 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 60s;
        proxy_request_buffering on;
    }
}
```

El firewall debe impedir conexiones públicas al puerto 8000. `ENFORCE_HTTPS=true` confía en `request.url.scheme` solo cuando Uvicorn acepta cabeceras reenviadas exclusivamente desde el proxy indicado. No configure `--forwarded-allow-ips '*'`.

Guarde `DB_PASSWORD` y `HMAC_MASTER_KEY` en el gestor de secretos. Haga copia coordinada de PostgreSQL y `IMAGE_STORAGE_ROOT`, monitorice lotes `procesando/fallido`, sincronice NTP y ejecute una tarea periódica para eliminar nonces expirados si el tráfico deja de activar la limpieza oportunista. No registre cabeceras HMAC ni cuerpos multipart en el proxy o APM.

