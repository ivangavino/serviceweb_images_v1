# Contrato API Mildiu v1

Todas las solicitudes se firman como explica el README. Ninguna respuesta expone credenciales, firmas, tokens temporales ni rutas absolutas.

## Registrar toma

`POST /api/v1/mildiu/tomas`, `Content-Type: application/json`. Una toma representa una visita al campo y contiene todos los surcos recorridos durante ella (hasta 20 con la configuración predeterminada). El ejemplo está en `examples/toma.json`. La fecha debe incluir offset; el calendario ISO se calcula en `CROP_TIMEZONE`. El servidor valida terreno activo y autorizado, cantidad de surcos, coordenadas, longitud PostGIS, resultados y límite semanal.

Respuesta `201`:

```json
{
  "success": true,
  "data": {
    "toma_id": "68fc4d25-9616-4919-99ef-ab0aa55f9980",
    "client_toma_id": "c4fa88bd-5cb7-4f91-b75b-37fe85d8c3c2",
    "estado": "recibida",
    "idempotent_replay": false,
    "surcos": [
      {
        "surco_id": "40c0af84-3b66-4aaa-9a37-88259cb0bd94",
        "numero_surco": 1,
        "codigo_surco": "SURCO-01",
        "longitud_metros": 70.1
      }
    ]
  }
}
```

## Subir lote

`POST /api/v1/mildiu/tomas/{toma_id}/surcos/{surco_id}/imagenes`, `multipart/form-data`.

`metadata` es una parte de texto JSON. Cada objeto nombra exactamente una parte binaria mediante `campo_archivo`; no se relacionan archivos por posición ni por nombre original.

```json
{
  "client_lote_id": "d390a872-a220-4e60-875d-cdf4c087ad94",
  "imagenes": [
    {
      "client_imagen_id": "908cb028-f027-4cd2-b1fb-0be058f33231",
      "campo_archivo": "imagen_908cb028f0274cd2b1fb0be058f33231",
      "categoria": "con_mildiu",
      "hash_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "fecha_captura": "2026-11-19T09:15:00-05:00"
    }
  ]
}
```

Categorías: `con_mildiu`, `sin_mildiu`, `posible_mildiu`. `hash_sha256` y `fecha_captura` son opcionales; el servidor siempre calcula el hash real. El lote solo puede pertenecer al prototipo dueño de la toma. Reutilizar el UUID del lote con otros metadatos o bytes da `409`.

## Finalizar

`POST /api/v1/mildiu/tomas/{toma_id}/finalizar` con cuerpo de cero bytes (su SHA-256 es `e3b0...b855`). Comprueba que las imágenes disponibles por categoría coincidan exactamente con los contadores declarados. Es idempotente si ya estaba completada. Una toma completada no acepta más cargas.

## Consultas

`GET /api/v1/mildiu/tomas/{toma_id}` devuelve el detalle completo y totales calculados desde los surcos. `GET /api/v1/mildiu/tomas/{toma_id}/estado` devuelve imágenes esperadas/recibidas, pendientes y archivos en recuperación. Ambos requieren cuerpo vacío y firma HMAC del método GET.

## Estados y códigos

- Toma: `recibida`, `procesando`, `completada`, `fallida`.
- Lote: `procesando`, `completado`, `fallido`.
- Archivo: `pendiente`, `disponible`, `fallido`.
- `401 authentication_failed`: hash, firma, timestamp o metadatos HMAC inválidos.
- `403 device_disabled`, `credential_inactive`, `terrain_forbidden`, `take_forbidden`.
- `409 nonce_reused`, `idempotency_conflict`, `weekly_take_number_conflict`, `weekly_limit_reached`, `image_count_mismatch`.
- `413 request_too_large`, `image_too_large`, `upload_too_large`, `too_many_files`.
- `415 unsupported_media_type`, `unsupported_image_format`.
- `422 invalid_take`, `invalid_metadata`, `invalid_image`, `invalid_furrow_length`.
- `503 database_unavailable`, `storage_recovery_pending`.

