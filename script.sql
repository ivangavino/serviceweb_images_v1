-- QuinuAlert - actualización manual del esquema para detección de mildiu
-- Fecha: 2026-11-20
--
-- SCRIPT COMPLEMENTARIO DE: esquema_quinua_iot.sql
-- El archivo esquema_quinua_iot.sql representa el esquema base que ya está en
-- producción. No es necesario modificarlo ni volver a ejecutarlo. Para agregar
-- el módulo de mildiu a producción se ejecuta únicamente este script.sql.
--
-- IMPORTANTE:
--   1. Realice una copia de seguridad de PostgreSQL antes de ejecutar.
--   2. Ejecute este archivo una sola vez sobre la base de datos de QuinuAlert.
--   3. En psql use: psql -v ON_ERROR_STOP=1 -d <base> -f script.sql
--   4. El script no modifica la estructura ni los datos de public.terrenos.
--   5. El script no registra prototipos, credenciales ni datos de prueba.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';
SET LOCAL search_path = public;

-- Validaciones previas. Ante cualquier incompatibilidad se revierte todo.
DO $preflight$
DECLARE
    v_terreno_id_type text;
    v_activo_type text;
    v_geometry_type text;
    v_geometry_srid integer;
    v_existing_tables text[];
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'postgis'
    ) THEN
        RAISE EXCEPTION
            'PostGIS no está instalado en esta base de datos. Instálelo antes de ejecutar script.sql.';
    END IF;

    IF to_regclass('public.terrenos') IS NULL THEN
        RAISE EXCEPTION
            'No existe public.terrenos. Verifique que está conectado a la base de datos correcta.';
    END IF;

    SELECT c.data_type
      INTO v_terreno_id_type
      FROM information_schema.columns AS c
     WHERE c.table_schema = 'public'
       AND c.table_name = 'terrenos'
       AND c.column_name = 'terreno_id';

    IF v_terreno_id_type IS DISTINCT FROM 'bigint' THEN
        RAISE EXCEPTION
            'public.terrenos.terreno_id debe ser BIGINT; tipo encontrado: %',
            COALESCE(v_terreno_id_type, '<columna inexistente>');
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint AS con
          JOIN pg_class AS rel ON rel.oid = con.conrelid
          JOIN pg_namespace AS nsp ON nsp.oid = rel.relnamespace
         WHERE nsp.nspname = 'public'
           AND rel.relname = 'terrenos'
           AND con.contype = 'p'
           AND pg_get_constraintdef(con.oid) = 'PRIMARY KEY (terreno_id)'
    ) THEN
        RAISE EXCEPTION
            'public.terrenos.terreno_id no es la clave primaria esperada.';
    END IF;

    SELECT c.data_type
      INTO v_activo_type
      FROM information_schema.columns AS c
     WHERE c.table_schema = 'public'
       AND c.table_name = 'terrenos'
       AND c.column_name = 'activo';

    IF v_activo_type IS DISTINCT FROM 'boolean' THEN
        RAISE EXCEPTION
            'public.terrenos.activo debe ser BOOLEAN; tipo encontrado: %',
            COALESCE(v_activo_type, '<columna inexistente>');
    END IF;

    SELECT postgis_typmod_type(att.atttypmod),
           postgis_typmod_srid(att.atttypmod)
      INTO v_geometry_type, v_geometry_srid
      FROM pg_attribute AS att
      JOIN pg_class AS rel ON rel.oid = att.attrelid
      JOIN pg_namespace AS nsp ON nsp.oid = rel.relnamespace
     WHERE nsp.nspname = 'public'
       AND rel.relname = 'terrenos'
       AND att.attname = 'poligono'
       AND NOT att.attisdropped;

    IF lower(v_geometry_type) IS DISTINCT FROM 'polygon'
       OR v_geometry_srid IS DISTINCT FROM 4326 THEN
        RAISE EXCEPTION
            'public.terrenos.poligono debe ser geometry(Polygon,4326); encontrado: geometry(%,%).',
            COALESCE(v_geometry_type, '<desconocido>'),
            COALESCE(v_geometry_srid::text, '<desconocido>');
    END IF;

    SELECT array_agg(t.table_name ORDER BY t.table_name)
      INTO v_existing_tables
      FROM information_schema.tables AS t
     WHERE t.table_schema = 'public'
       AND t.table_name = ANY (ARRAY[
           'prototipos_mildiu',
           'credenciales_prototipo_mildiu',
           'prototipos_terrenos_mildiu',
           'nonces_mildiu',
           'tomas_mildiu',
           'surcos_mildiu',
           'resultados_mildiu',
           'lotes_imagenes_mildiu',
           'imagenes_mildiu'
       ]);

    IF v_existing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'La actualización no se ejecutó porque ya existen tablas del módulo mildiu: %. Revise el esquema antes de reintentar.',
            array_to_string(v_existing_tables, ', ');
    END IF;
END
$preflight$;

CREATE TABLE public.prototipos_mildiu (
    prototipo_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo varchar(100) NOT NULL,
    nombre varchar(150) NOT NULL,
    descripcion text,
    estado varchar(20) NOT NULL DEFAULT 'activo',
    creado_en timestamptz NOT NULL DEFAULT now(),
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    ultima_comunicacion timestamptz,
    CONSTRAINT uq_prototipos_mildiu_codigo UNIQUE (codigo),
    CONSTRAINT ck_prototipos_mildiu_estado
        CHECK (estado IN ('activo', 'inactivo', 'mantenimiento', 'revocado'))
);

CREATE TABLE public.credenciales_prototipo_mildiu (
    credencial_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prototipo_id bigint NOT NULL,
    key_id varchar(100) NOT NULL,
    secreto_cifrado bytea NOT NULL,
    estado varchar(20) NOT NULL DEFAULT 'activa',
    valida_desde timestamptz NOT NULL DEFAULT now(),
    valida_hasta timestamptz,
    creado_en timestamptz NOT NULL DEFAULT now(),
    rotada_en timestamptz,
    CONSTRAINT fk_credencial_mildiu_prototipo
        FOREIGN KEY (prototipo_id)
        REFERENCES public.prototipos_mildiu (prototipo_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT uq_credencial_prototipo_key
        UNIQUE (prototipo_id, key_id),
    CONSTRAINT ck_credencial_mildiu_estado
        CHECK (estado IN ('activa', 'retirada', 'revocada')),
    CONSTRAINT ck_credencial_mildiu_vigencia
        CHECK (valida_hasta IS NULL OR valida_hasta > valida_desde)
);

CREATE TABLE public.prototipos_terrenos_mildiu (
    prototipo_id bigint NOT NULL,
    terreno_id bigint NOT NULL,
    activo boolean NOT NULL DEFAULT true,
    creado_en timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_prototipos_terrenos_mildiu
        PRIMARY KEY (prototipo_id, terreno_id),
    CONSTRAINT fk_prototipo_terreno_mildiu_prototipo
        FOREIGN KEY (prototipo_id)
        REFERENCES public.prototipos_mildiu (prototipo_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT fk_prototipo_terreno_mildiu_terreno
        FOREIGN KEY (terreno_id)
        REFERENCES public.terrenos (terreno_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

CREATE TABLE public.nonces_mildiu (
    nonce_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prototipo_id bigint NOT NULL,
    credencial_id bigint NOT NULL,
    nonce varchar(128) NOT NULL,
    timestamp_solicitud bigint NOT NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    expira_en timestamptz NOT NULL,
    CONSTRAINT fk_nonce_mildiu_prototipo
        FOREIGN KEY (prototipo_id)
        REFERENCES public.prototipos_mildiu (prototipo_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT fk_nonce_mildiu_credencial
        FOREIGN KEY (credencial_id)
        REFERENCES public.credenciales_prototipo_mildiu (credencial_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT uq_nonce_mildiu_credencial
        UNIQUE (prototipo_id, credencial_id, nonce),
    CONSTRAINT ck_nonce_mildiu_expiracion
        CHECK (expira_en > creado_en)
);

CREATE INDEX ix_nonces_mildiu_expira
    ON public.nonces_mildiu (expira_en);

CREATE TABLE public.tomas_mildiu (
    toma_id uuid PRIMARY KEY,
    client_toma_id uuid NOT NULL,
    terreno_id bigint NOT NULL,
    prototipo_id bigint NOT NULL,
    numero_toma_semana smallint NOT NULL,
    fecha_inicio timestamptz NOT NULL,
    fecha_fin timestamptz,
    semana_iso smallint NOT NULL,
    anio_iso integer NOT NULL,
    cantidad_surcos smallint NOT NULL,
    estado varchar(20) NOT NULL DEFAULT 'recibida',
    descripcion text,
    contenido_sha256 varchar(64) NOT NULL,
    ultimo_error_recuperable varchar(500),
    creado_en timestamptz NOT NULL DEFAULT now(),
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_toma_mildiu_terreno
        FOREIGN KEY (terreno_id)
        REFERENCES public.terrenos (terreno_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT fk_toma_mildiu_prototipo
        FOREIGN KEY (prototipo_id)
        REFERENCES public.prototipos_mildiu (prototipo_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT uq_toma_mildiu_cliente
        UNIQUE (prototipo_id, client_toma_id),
    CONSTRAINT uq_toma_mildiu_semana_numero
        UNIQUE (terreno_id, anio_iso, semana_iso, numero_toma_semana),
    CONSTRAINT ck_toma_mildiu_numero
        CHECK (numero_toma_semana > 0),
    CONSTRAINT ck_toma_mildiu_semana
        CHECK (semana_iso BETWEEN 1 AND 53),
    CONSTRAINT ck_toma_mildiu_surcos
        CHECK (cantidad_surcos > 0),
    CONSTRAINT ck_toma_mildiu_fechas
        CHECK (fecha_fin IS NULL OR fecha_fin >= fecha_inicio),
    CONSTRAINT ck_toma_mildiu_hash
        CHECK (contenido_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_toma_mildiu_estado
        CHECK (estado IN ('recibida', 'procesando', 'completada', 'fallida'))
);

CREATE INDEX ix_tomas_mildiu_terreno_fecha
    ON public.tomas_mildiu (terreno_id, fecha_inicio);

CREATE INDEX ix_tomas_mildiu_prototipo_fecha
    ON public.tomas_mildiu (prototipo_id, fecha_inicio);

CREATE TABLE public.surcos_mildiu (
    surco_id uuid PRIMARY KEY,
    toma_id uuid NOT NULL,
    numero_surco smallint NOT NULL,
    codigo_surco varchar(100) NOT NULL,
    latitud_inicio numeric(10, 7) NOT NULL,
    longitud_inicio numeric(10, 7) NOT NULL,
    latitud_fin numeric(10, 7) NOT NULL,
    longitud_fin numeric(10, 7) NOT NULL,
    longitud_metros double precision NOT NULL,
    geometria geometry(LineString, 4326) NOT NULL,
    descripcion text,
    creado_en timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_surco_mildiu_toma
        FOREIGN KEY (toma_id)
        REFERENCES public.tomas_mildiu (toma_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT uq_surco_mildiu_numero
        UNIQUE (toma_id, numero_surco),
    CONSTRAINT uq_surco_mildiu_codigo
        UNIQUE (toma_id, codigo_surco),
    CONSTRAINT ck_surco_lat_inicio
        CHECK (latitud_inicio BETWEEN -90 AND 90),
    CONSTRAINT ck_surco_lat_fin
        CHECK (latitud_fin BETWEEN -90 AND 90),
    CONSTRAINT ck_surco_lon_inicio
        CHECK (longitud_inicio BETWEEN -180 AND 180),
    CONSTRAINT ck_surco_lon_fin
        CHECK (longitud_fin BETWEEN -180 AND 180),
    CONSTRAINT ck_surco_longitud_metros
        CHECK (longitud_metros > 0),
    CONSTRAINT ck_surco_geometria_srid
        CHECK (ST_SRID(geometria) = 4326),
    CONSTRAINT ck_surco_geometria_tipo
        CHECK (GeometryType(geometria) = 'LINESTRING'),
    CONSTRAINT ck_surco_geometria_no_vacia
        CHECK (NOT ST_IsEmpty(geometria)),
    CONSTRAINT ck_surco_geometria_valida
        CHECK (ST_IsValid(geometria))
);

CREATE INDEX ix_surcos_mildiu_geometria
    ON public.surcos_mildiu
    USING gist (geometria);

CREATE TABLE public.resultados_mildiu (
    resultado_id uuid PRIMARY KEY,
    surco_id uuid NOT NULL,
    total_imagenes integer NOT NULL,
    imagenes_procesadas integer NOT NULL,
    imagenes_con_mildiu integer NOT NULL,
    imagenes_sin_mildiu integer NOT NULL,
    imagenes_posible_mildiu integer NOT NULL,
    imagenes_no_procesadas integer NOT NULL,
    modelo_ia varchar(150) NOT NULL,
    version_modelo varchar(80) NOT NULL,
    fecha_procesamiento timestamptz NOT NULL,
    creado_en timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_resultado_mildiu_surco
        FOREIGN KEY (surco_id)
        REFERENCES public.surcos_mildiu (surco_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT uq_resultado_mildiu_surco
        UNIQUE (surco_id),
    CONSTRAINT ck_resultados_mildiu_no_negativos
        CHECK (
            total_imagenes >= 0
            AND imagenes_procesadas >= 0
            AND imagenes_con_mildiu >= 0
            AND imagenes_sin_mildiu >= 0
            AND imagenes_posible_mildiu >= 0
            AND imagenes_no_procesadas >= 0
        ),
    CONSTRAINT ck_resultados_mildiu_total
        CHECK (total_imagenes >= imagenes_procesadas),
    CONSTRAINT ck_resultados_mildiu_clasificacion
        CHECK (
            imagenes_procesadas =
            imagenes_con_mildiu
            + imagenes_sin_mildiu
            + imagenes_posible_mildiu
        ),
    CONSTRAINT ck_resultados_mildiu_no_procesadas
        CHECK (
            imagenes_no_procesadas = total_imagenes - imagenes_procesadas
        )
);

CREATE TABLE public.lotes_imagenes_mildiu (
    lote_id uuid PRIMARY KEY,
    client_lote_id uuid NOT NULL,
    prototipo_id bigint NOT NULL,
    toma_id uuid NOT NULL,
    surco_id uuid NOT NULL,
    contenido_sha256 varchar(64) NOT NULL,
    cantidad_imagenes integer NOT NULL,
    estado varchar(20) NOT NULL DEFAULT 'procesando',
    ultimo_error varchar(500),
    creado_en timestamptz NOT NULL DEFAULT now(),
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_lote_mildiu_prototipo
        FOREIGN KEY (prototipo_id)
        REFERENCES public.prototipos_mildiu (prototipo_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT fk_lote_mildiu_toma
        FOREIGN KEY (toma_id)
        REFERENCES public.tomas_mildiu (toma_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT fk_lote_mildiu_surco
        FOREIGN KEY (surco_id)
        REFERENCES public.surcos_mildiu (surco_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT uq_lote_mildiu_cliente
        UNIQUE (prototipo_id, client_lote_id),
    CONSTRAINT ck_lote_mildiu_cantidad
        CHECK (cantidad_imagenes > 0),
    CONSTRAINT ck_lote_mildiu_hash
        CHECK (contenido_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_lote_mildiu_estado
        CHECK (estado IN ('procesando', 'completado', 'fallido'))
);

CREATE INDEX ix_lotes_mildiu_toma_surco
    ON public.lotes_imagenes_mildiu (toma_id, surco_id);

CREATE TABLE public.imagenes_mildiu (
    imagen_id uuid PRIMARY KEY,
    client_imagen_id uuid NOT NULL,
    resultado_id uuid NOT NULL,
    lote_id uuid NOT NULL,
    nombre_original varchar(255) NOT NULL,
    nombre_almacenado varchar(100) NOT NULL,
    ruta_relativa varchar(1000) NOT NULL,
    token_temporal varchar(100),
    categoria varchar(30) NOT NULL,
    formato varchar(10) NOT NULL,
    tamano_bytes bigint NOT NULL,
    hash_sha256 varchar(64) NOT NULL,
    fecha_captura timestamptz,
    estado_almacenamiento varchar(20) NOT NULL DEFAULT 'pendiente',
    creado_en timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_imagen_mildiu_resultado
        FOREIGN KEY (resultado_id)
        REFERENCES public.resultados_mildiu (resultado_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT fk_imagen_mildiu_lote
        FOREIGN KEY (lote_id)
        REFERENCES public.lotes_imagenes_mildiu (lote_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT uq_imagen_mildiu_cliente
        UNIQUE (resultado_id, client_imagen_id),
    CONSTRAINT uq_imagen_mildiu_hash
        UNIQUE (resultado_id, hash_sha256),
    CONSTRAINT ck_imagen_mildiu_categoria
        CHECK (categoria IN ('con_mildiu', 'sin_mildiu', 'posible_mildiu')),
    CONSTRAINT ck_imagen_mildiu_formato
        CHECK (formato IN ('JPEG', 'PNG')),
    CONSTRAINT ck_imagen_mildiu_tamano
        CHECK (tamano_bytes > 0),
    CONSTRAINT ck_imagen_mildiu_hash
        CHECK (hash_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_imagen_mildiu_ruta_relativa
        CHECK (
            ruta_relativa <> ''
            AND ruta_relativa !~ '(^|/)[.][.](/|$)'
            AND ruta_relativa !~ '^/'
            AND ruta_relativa !~ '^[A-Za-z]:[\\/]'
        ),
    CONSTRAINT ck_imagen_mildiu_almacenamiento
        CHECK (estado_almacenamiento IN ('pendiente', 'disponible', 'fallido'))
);

CREATE INDEX ix_imagenes_mildiu_categoria
    ON public.imagenes_mildiu (categoria);

CREATE INDEX ix_imagenes_mildiu_lote
    ON public.imagenes_mildiu (lote_id);

COMMENT ON TABLE public.prototipos_mildiu IS
    'Prototipos Raspberry Pi autorizados para enviar resultados de mildiu.';
COMMENT ON COLUMN public.credenciales_prototipo_mildiu.secreto_cifrado IS
    'Secreto HMAC cifrado por la aplicación; nunca contiene texto plano.';
COMMENT ON TABLE public.nonces_mildiu IS
    'Nonces consumidos para impedir la repetición de solicitudes HMAC.';
COMMENT ON COLUMN public.imagenes_mildiu.ruta_relativa IS
    'Ruta relativa a IMAGE_STORAGE_ROOT; nunca debe contener una ruta absoluta.';

-- Comprobación final antes de confirmar la transacción.
DO $verification$
DECLARE
    v_created_count integer;
BEGIN
    SELECT count(*)
      INTO v_created_count
      FROM information_schema.tables AS t
     WHERE t.table_schema = 'public'
       AND t.table_name = ANY (ARRAY[
           'prototipos_mildiu',
           'credenciales_prototipo_mildiu',
           'prototipos_terrenos_mildiu',
           'nonces_mildiu',
           'tomas_mildiu',
           'surcos_mildiu',
           'resultados_mildiu',
           'lotes_imagenes_mildiu',
           'imagenes_mildiu'
       ]);

    IF v_created_count <> 9 THEN
        RAISE EXCEPTION
            'Verificación final fallida: se esperaban 9 tablas y se encontraron %.',
            v_created_count;
    END IF;
END
$verification$;

COMMIT;

-- Verificación informativa posterior (solo lectura).
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema = 'public'
   AND table_name LIKE '%mildiu%'
 ORDER BY table_name;

