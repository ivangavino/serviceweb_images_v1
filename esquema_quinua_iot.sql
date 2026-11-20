-- Base de datos para monitoreo IoT de cultivos de quinua.
-- PostgreSQL 14+ y PostGIS 3+. Ejecutar con una cuenta que pueda crear extensiones.

BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;

-- Se emplean TEXT + CHECK en los estados: son valores cortos y controlados por la API.
-- Si el negocio necesita administrarlos sin despliegues, reemplácelos por catálogos con FK.

CREATE TABLE propietarios (
  propietario_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nombres TEXT NOT NULL,
  apellidos TEXT NOT NULL,
  dni VARCHAR(20) NOT NULL UNIQUE,
  telefono VARCHAR(30),
  email TEXT NOT NULL UNIQUE,
  contrasena_hash TEXT NOT NULL,
  tipo_usuario TEXT NOT NULL DEFAULT 'Administrador'
    CHECK (tipo_usuario IN ('Administrador','Propietario','Visualizador')),
  estado TEXT NOT NULL DEFAULT 'activo'
    CHECK (estado IN ('activo', 'inactivo', 'suspendido')),
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT propietarios_dni_no_vacio CHECK (btrim(dni) <> ''),
  CONSTRAINT propietarios_email_no_vacio CHECK (btrim(email) <> '')
);
COMMENT ON COLUMN propietarios.contrasena_hash IS
  'Hash adaptativo (Argon2id/bcrypt) generado por la aplicación; jamás guardar texto plano.';

CREATE TABLE terrenos (
  terreno_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  propietario_id BIGINT NOT NULL REFERENCES propietarios(propietario_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  descripcion TEXT NOT NULL,
  direccion TEXT,
  area DOUBLE PRECISION NOT NULL,
  poligono geometry(Polygon, 4326) NOT NULL,
  activo BOOLEAN NOT NULL DEFAULT true,
  actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT terrenos_poligono_valido CHECK (ST_IsValid(poligono)),
  CONSTRAINT terrenos_poligono_sin_vacio CHECK (NOT ST_IsEmpty(poligono))
);
COMMENT ON COLUMN terrenos.poligono IS 'Límite WGS84 del terreno; anillo exterior sin auto-intersecciones.';

CREATE TABLE estaciones_meteorologicas (
  estacion_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  terreno_id BIGINT NOT NULL REFERENCES terrenos(terreno_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  codigo VARCHAR(80) NOT NULL UNIQUE,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  ubicacion geometry(Point, 4326) NOT NULL,
  latitud DOUBLE PRECISION GENERATED ALWAYS AS (ST_Y(ubicacion)) STORED,
  longitud DOUBLE PRECISION GENERATED ALWAYS AS (ST_X(ubicacion)) STORED,
  bateria_actual NUMERIC(5,2),
  estado TEXT NOT NULL DEFAULT 'activa'
    CHECK (estado IN ('activa', 'inactiva', 'mantenimiento', 'fuera_de_linea', 'retirada')),
  ultima_comunicacion TIMESTAMPTZ,
  CONSTRAINT estaciones_codigo_no_vacio CHECK (btrim(codigo) <> ''),
  CONSTRAINT estaciones_ubicacion_valida CHECK (
    ST_Y(ubicacion) BETWEEN -90 AND 90 AND ST_X(ubicacion) BETWEEN -180 AND 180
  ),
  CONSTRAINT estaciones_bateria_rango CHECK (bateria_actual IS NULL OR bateria_actual BETWEEN 0 AND 100)
);
COMMENT ON COLUMN estaciones_meteorologicas.bateria_actual IS
  'Caché desnormalizada opcional del último historial; actualizar en la misma transacción de la telemetría.';

CREATE TABLE historial_bateria_estacion (
  historial_bateria_estacion_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  estacion_id BIGINT NOT NULL REFERENCES estaciones_meteorologicas(estacion_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  porcentaje NUMERIC(5,2) NOT NULL CHECK (porcentaje BETWEEN 0 AND 100),
  voltaje NUMERIC(10,3) CHECK (voltaje >= 0),
  estado_carga TEXT NOT NULL CHECK (estado_carga IN ('cargando', 'descargando', 'completa', 'sin_datos', 'error')),
  registrado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE historial_ubicacion_estacion (
  ubicacion_estacion_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  estacion_id BIGINT NOT NULL REFERENCES estaciones_meteorologicas(estacion_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  ubicacion geometry(Point, 4326) NOT NULL,
  latitud DOUBLE PRECISION GENERATED ALWAYS AS (ST_Y(ubicacion)) STORED,
  longitud DOUBLE PRECISION GENERATED ALWAYS AS (ST_X(ubicacion)) STORED,
  precision NUMERIC(10,3) CHECK (precision >= 0),
  registrado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ubicacion_estacion_coordenadas_validas CHECK (
    ST_Y(ubicacion) BETWEEN -90 AND 90 AND ST_X(ubicacion) BETWEEN -180 AND 180
  )
);

CREATE TABLE dispositivos_moviles (
  dispositivo_movil_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  estacion_id BIGINT NOT NULL REFERENCES estaciones_meteorologicas(estacion_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  codigo VARCHAR(80) NOT NULL UNIQUE,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  ubicacion geometry(Point, 4326),
  latitud DOUBLE PRECISION GENERATED ALWAYS AS (CASE WHEN ubicacion IS NULL THEN NULL ELSE ST_Y(ubicacion) END) STORED,
  longitud DOUBLE PRECISION GENERATED ALWAYS AS (CASE WHEN ubicacion IS NULL THEN NULL ELSE ST_X(ubicacion) END) STORED,
  bateria_actual NUMERIC(5,2),
  estado TEXT NOT NULL DEFAULT 'activo'
    CHECK (estado IN ('activo', 'inactivo', 'mantenimiento', 'fuera_de_linea', 'retirado')),
  ultima_comunicacion TIMESTAMPTZ,
  CONSTRAINT dispositivos_codigo_no_vacio CHECK (btrim(codigo) <> ''),
  CONSTRAINT dispositivos_ubicacion_valida CHECK (ubicacion IS NULL OR
    (ST_Y(ubicacion) BETWEEN -90 AND 90 AND ST_X(ubicacion) BETWEEN -180 AND 180)),
  CONSTRAINT dispositivos_bateria_rango CHECK (bateria_actual IS NULL OR bateria_actual BETWEEN 0 AND 100)
);

CREATE TABLE tipos_sensor (
  tipo_sensor_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nombre TEXT NOT NULL UNIQUE,
  unidad_medida TEXT NOT NULL,
  categoria TEXT NOT NULL,
  valor_minimo DOUBLE PRECISION,
  valor_maximo DOUBLE PRECISION,
  CONSTRAINT tipos_sensor_rango_valido CHECK (
    valor_minimo IS NULL OR valor_maximo IS NULL OR valor_minimo <= valor_maximo
  )
);

CREATE TABLE sensores (
  sensor_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tipo_sensor_id BIGINT NOT NULL REFERENCES tipos_sensor(tipo_sensor_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  estacion_id BIGINT REFERENCES estaciones_meteorologicas(estacion_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  dispositivo_movil_id BIGINT REFERENCES dispositivos_moviles(dispositivo_movil_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  codigo VARCHAR(80) NOT NULL UNIQUE,
  modelo TEXT,
  numero_serie TEXT UNIQUE,
  estado TEXT NOT NULL DEFAULT 'activo'
    CHECK (estado IN ('activo', 'inactivo', 'calibracion', 'mantenimiento', 'fallido', 'retirado')),
  instalado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT sensores_un_solo_contenedor CHECK (
    (estacion_id IS NOT NULL AND dispositivo_movil_id IS NULL)
    OR (estacion_id IS NULL AND dispositivo_movil_id IS NOT NULL)
  ),
  CONSTRAINT sensores_codigo_no_vacio CHECK (btrim(codigo) <> '')
);
COMMENT ON COLUMN sensores.numero_serie IS 'UNIQUE recomendado: identifica físicamente el sensor y evita inventario duplicado.';

-- La partición evita que la tabla de telemetría se convierta en un único objeto inmanejable.
-- La PK incluye fecha_hora porque PostgreSQL exige la clave de partición en PK/UNIQUE globales.
CREATE TABLE mediciones (
  medicion_id BIGINT GENERATED ALWAYS AS IDENTITY,
  sensor_id BIGINT NOT NULL REFERENCES sensores(sensor_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  valor DOUBLE PRECISION NOT NULL,
  calidad TEXT NOT NULL DEFAULT 'valida'
    CHECK (calidad IN ('valida', 'sospechosa', 'invalida', 'estimada', 'sin_calibrar')),
  fecha_hora TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (fecha_hora, medicion_id)
) PARTITION BY RANGE (fecha_hora);

-- Partición de seguridad. En producción crear particiones mensuales por adelantado y vaciar esta regularmente.
CREATE TABLE mediciones_predeterminada PARTITION OF mediciones DEFAULT;

CREATE TABLE errores_sensor (
  error_sensor_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  sensor_id BIGINT NOT NULL REFERENCES sensores(sensor_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  codigo_error VARCHAR(80) NOT NULL,
  descripcion TEXT NOT NULL,
  severidad TEXT NOT NULL CHECK (severidad IN ('info', 'advertencia', 'error', 'critica')),
  resuelto BOOLEAN NOT NULL DEFAULT false,
  detectado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  resuelto_en TIMESTAMPTZ,
  CONSTRAINT errores_sensor_resolucion_coherente CHECK (
    (resuelto = false AND resuelto_en IS NULL) OR (resuelto = true AND resuelto_en IS NOT NULL)
  )
);

CREATE TABLE historial_bateria_movil (
  bateria_movil_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dispositivo_movil_id BIGINT NOT NULL REFERENCES dispositivos_moviles(dispositivo_movil_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  porcentaje NUMERIC(5,2) NOT NULL CHECK (porcentaje BETWEEN 0 AND 100),
  voltaje NUMERIC(10,3) CHECK (voltaje >= 0),
  estado_carga TEXT NOT NULL CHECK (estado_carga IN ('cargando', 'descargando', 'completa', 'sin_datos', 'error')),
  registrado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE historial_ubicacion_movil (
  ubicacion_movil_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dispositivo_movil_id BIGINT NOT NULL REFERENCES dispositivos_moviles(dispositivo_movil_id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  ubicacion geometry(Point, 4326) NOT NULL,
  latitud DOUBLE PRECISION GENERATED ALWAYS AS (ST_Y(ubicacion)) STORED,
  longitud DOUBLE PRECISION GENERATED ALWAYS AS (ST_X(ubicacion)) STORED,
  precision NUMERIC(10,3) CHECK (precision >= 0),
  registrado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ubicacion_movil_coordenadas_validas CHECK (
    ST_Y(ubicacion) BETWEEN -90 AND 90 AND ST_X(ubicacion) BETWEEN -180 AND 180
  )
);

-- Índices relacionales, temporales, parciales y espaciales.
CREATE INDEX idx_terrenos_propietario ON terrenos (propietario_id);
CREATE INDEX idx_terrenos_poligono_gist ON terrenos USING GIST (poligono);
CREATE INDEX idx_estaciones_terreno ON estaciones_meteorologicas (terreno_id);
CREATE INDEX idx_estaciones_ubicacion_gist ON estaciones_meteorologicas USING GIST (ubicacion);
CREATE INDEX idx_dispositivos_estacion ON dispositivos_moviles (estacion_id);
CREATE INDEX idx_dispositivos_ubicacion_gist ON dispositivos_moviles USING GIST (ubicacion);
CREATE INDEX idx_sensores_estacion ON sensores (estacion_id) WHERE estacion_id IS NOT NULL;
CREATE INDEX idx_sensores_dispositivo ON sensores (dispositivo_movil_id) WHERE dispositivo_movil_id IS NOT NULL;
CREATE INDEX idx_sensores_tipo ON sensores (tipo_sensor_id);
CREATE INDEX idx_mediciones_sensor_fecha ON mediciones (sensor_id, fecha_hora DESC);
CREATE INDEX idx_mediciones_fecha ON mediciones (fecha_hora DESC);
CREATE INDEX idx_bateria_estacion_fecha ON historial_bateria_estacion (estacion_id, registrado_en DESC);
CREATE INDEX idx_ubicacion_estacion_fecha ON historial_ubicacion_estacion (estacion_id, registrado_en DESC);
CREATE INDEX idx_ubicacion_estacion_gist ON historial_ubicacion_estacion USING GIST (ubicacion);
CREATE INDEX idx_bateria_movil_fecha ON historial_bateria_movil (dispositivo_movil_id, registrado_en DESC);
CREATE INDEX idx_ubicacion_movil_fecha ON historial_ubicacion_movil (dispositivo_movil_id, registrado_en DESC);
CREATE INDEX idx_ubicacion_movil_gist ON historial_ubicacion_movil USING GIST (ubicacion);
CREATE INDEX idx_errores_sensor_pendientes ON errores_sensor (sensor_id, detectado_en DESC) WHERE NOT resuelto;


-- CREATE TABLE mediciones_2026_10 PARTITION OF mediciones
--   FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');

CREATE TABLE IF NOT EXISTS dispositivos_api (
    dispositivo_api_id BIGSERIAL PRIMARY KEY,
    device_id VARCHAR(100) NOT NULL UNIQUE,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    descripcion TEXT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ultima_comunicacion TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS nonces_utilizados (
    nonce_id BIGSERIAL PRIMARY KEY,
    device_id VARCHAR(100) NOT NULL,
    nonce VARCHAR(100) NOT NULL,
    timestamp_solicitud BIGINT NOT NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(device_id, nonce)
);
CREATE INDEX IF NOT EXISTS ix_nonces_utilizados_creado_en ON nonces_utilizados(creado_en);

-- Gestión persistente de alertas y trazabilidad. Ejecutar una vez sobre PostgreSQL.
-- No genera alertas ni modifica telemetría existente; el integrador de reglas debe insertar
-- alertas en esta tabla cuando disponga de reglas oficiales.

CREATE TABLE IF NOT EXISTS alertas (
  alerta_id BIGSERIAL PRIMARY KEY,
  titulo TEXT NOT NULL,
  descripcion TEXT NOT NULL,
  prioridad TEXT NOT NULL CHECK (prioridad IN ('critica','advertencia','informativa')),
  estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','reconocida','resuelta')),
  origen_tipo TEXT NOT NULL CHECK (origen_tipo IN ('estacion','dispositivo_movil')),
  estacion_id BIGINT REFERENCES estaciones_meteorologicas(estacion_id),
  dispositivo_movil_id BIGINT REFERENCES dispositivos_moviles(dispositivo_movil_id),
  sensor_id BIGINT REFERENCES sensores(sensor_id),
  valor DOUBLE PRECISION,
  unidad TEXT,
  condicion TEXT,
  detectada_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  reconocida_en TIMESTAMPTZ,
  reconocida_por_id BIGINT REFERENCES propietarios(propietario_id),
  resuelta_en TIMESTAMPTZ,
  resuelta_por_id BIGINT REFERENCES propietarios(propietario_id),
  version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS alertas_detectada_en_idx ON alertas (detectada_en DESC);
CREATE INDEX IF NOT EXISTS alertas_estado_idx ON alertas (estado, prioridad);

CREATE TABLE IF NOT EXISTS alertas_eventos (
  evento_id BIGSERIAL PRIMARY KEY,
  alerta_id BIGINT REFERENCES alertas(alerta_id) ON DELETE RESTRICT,
  tipo TEXT NOT NULL CHECK (tipo IN ('deteccion','reconocimiento','resolucion','nota','evento')),
  descripcion TEXT NOT NULL,
  origen_tipo TEXT CHECK (origen_tipo IN ('estacion','dispositivo_movil')),
  estacion_id BIGINT REFERENCES estaciones_meteorologicas(estacion_id),
  dispositivo_movil_id BIGINT REFERENCES dispositivos_moviles(dispositivo_movil_id),
  usuario_id BIGINT REFERENCES propietarios(propietario_id),
  creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alertas_eventos_alerta_idx ON alertas_eventos (alerta_id, creado_en DESC);
CREATE INDEX IF NOT EXISTS alertas_eventos_creado_en_idx ON alertas_eventos (creado_en DESC);


-- Evoluciona el almacenamiento de alertas a episodios de diagnóstico.
-- Las referencias a telemetría y usuarios son instantáneas informativas: no tienen claves foráneas.

-- BEGIN;

-- Se eliminan para que una alerta conserve
-- su trazabilidad aunque cambie o se elimine el registro operativo.

DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT conname FROM pg_constraint WHERE conrelid = 'alertas'::regclass AND contype = 'f' LOOP
    EXECUTE format('ALTER TABLE alertas DROP CONSTRAINT %I', r.conname);
  END LOOP;
  FOR r IN SELECT conname FROM pg_constraint WHERE conrelid = 'alertas_eventos'::regclass AND contype = 'f' LOOP
    EXECUTE format('ALTER TABLE alertas_eventos DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$;

ALTER TABLE alertas
  ADD COLUMN IF NOT EXISTS clave_condicion TEXT,
  ADD COLUMN IF NOT EXISTS tipo_alerta TEXT,
  ADD COLUMN IF NOT EXISTS codigo_diagnostico TEXT,
  ADD COLUMN IF NOT EXISTS condicion_tecnica TEXT NOT NULL DEFAULT 'activa',
  ADD COLUMN IF NOT EXISTS origen_referencia_id BIGINT,
  ADD COLUMN IF NOT EXISTS origen_codigo_snapshot TEXT,
  ADD COLUMN IF NOT EXISTS origen_nombre_snapshot TEXT,
  ADD COLUMN IF NOT EXISTS contenedor_tipo TEXT,
  ADD COLUMN IF NOT EXISTS contenedor_nombre TEXT,
  ADD COLUMN IF NOT EXISTS medicion_referencia_id BIGINT,
  ADD COLUMN IF NOT EXISTS fecha_dato TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS primera_deteccion TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS ultima_deteccion TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS recuperada_en TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS limites JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS metadatos JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now();

UPDATE alertas SET
  clave_condicion = COALESCE(clave_condicion, condicion, 'legado-' || alerta_id::text),
  tipo_alerta = COALESCE(tipo_alerta, 'bateria'),
  codigo_diagnostico = COALESCE(codigo_diagnostico, 'legado'),
  -- Los registros heredados sin origen conservan un identificador negativo
  -- informativo para que la migración no invente una relación operativa.
  origen_referencia_id = COALESCE(origen_referencia_id, sensor_id, estacion_id, dispositivo_movil_id, -alerta_id),
  origen_codigo_snapshot = COALESCE(origen_codigo_snapshot, ''),
  origen_nombre_snapshot = COALESCE(origen_nombre_snapshot, titulo),
  primera_deteccion = COALESCE(primera_deteccion, detectada_en),
  ultima_deteccion = COALESCE(ultima_deteccion, detectada_en),
  fecha_dato = COALESCE(fecha_dato, detectada_en);

ALTER TABLE alertas ALTER COLUMN clave_condicion SET NOT NULL;
ALTER TABLE alertas ALTER COLUMN tipo_alerta SET NOT NULL;
ALTER TABLE alertas ALTER COLUMN codigo_diagnostico SET NOT NULL;
ALTER TABLE alertas ALTER COLUMN origen_referencia_id SET NOT NULL;

ALTER TABLE alertas DROP CONSTRAINT IF EXISTS alertas_origen_tipo_check;
ALTER TABLE alertas ADD CONSTRAINT alertas_origen_tipo_check CHECK (origen_tipo IN ('sensor','estacion','dispositivo_movil'));
ALTER TABLE alertas ADD CONSTRAINT alertas_tipo_alerta_check CHECK (tipo_alerta IN ('lectura','bateria'));
ALTER TABLE alertas ADD CONSTRAINT alertas_condicion_tecnica_check CHECK (condicion_tecnica IN ('activa','recuperada'));

ALTER TABLE alertas_eventos
  ADD COLUMN IF NOT EXISTS estado_anterior TEXT,
  ADD COLUMN IF NOT EXISTS estado_nuevo TEXT,
  ADD COLUMN IF NOT EXISTS condicion_anterior TEXT,
  ADD COLUMN IF NOT EXISTS condicion_nueva TEXT,
  ADD COLUMN IF NOT EXISTS usuario_nombre_snapshot TEXT,
  ADD COLUMN IF NOT EXISTS metadatos JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE alertas_eventos DROP CONSTRAINT IF EXISTS alertas_eventos_tipo_check;
ALTER TABLE alertas_eventos ADD CONSTRAINT alertas_eventos_tipo_check CHECK (tipo IN ('deteccion','cambio_condicion','reconocimiento','resolucion','recuperacion','nota','evento'));
-- ALTER TABLE alertas_eventos DROP CONSTRAINT IF EXISTS alertas_eventos_alerta_id_fkey;
ALTER TABLE alertas_eventos ADD CONSTRAINT alertas_eventos_alerta_id_fkey
  FOREIGN KEY (alerta_id) REFERENCES alertas(alerta_id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS alertas_origen_idx ON alertas (origen_tipo, origen_referencia_id);
CREATE INDEX IF NOT EXISTS alertas_fecha_dato_idx ON alertas (fecha_dato DESC);
CREATE INDEX IF NOT EXISTS alertas_activas_idx ON alertas (condicion_tecnica, prioridad) WHERE condicion_tecnica='activa';
CREATE UNIQUE INDEX IF NOT EXISTS alertas_episodio_activo_unico
  ON alertas (origen_tipo, origen_referencia_id, clave_condicion)
  WHERE condicion_tecnica='activa';
CREATE INDEX IF NOT EXISTS alertas_eventos_fecha_idx ON alertas_eventos (alerta_id, creado_en DESC);

COMMENT ON COLUMN alertas.origen_referencia_id IS 'Identificador operativo informativo; no es una clave foránea.';
COMMENT ON COLUMN alertas.limites IS 'Instantánea JSON de rango o umbrales usados para el diagnóstico.';
COMMENT ON COLUMN alertas.condicion_tecnica IS 'Estado físico inferido: activa o recuperada; es independiente del estado de atención.';
COMMENT ON COLUMN alertas_eventos.usuario_id IS 'Identificador informativo del usuario; no es una clave foránea.';

COMMIT;

-- Permite que el historial registre acciones de alertas cuyo origen es un sensor.
-- La restricción anterior provenía de 001 y rechazaba notas, reconocimientos y
-- resoluciones aunque alertas.origen_tipo ya admitiera 'sensor'.
BEGIN;
ALTER TABLE alertas_eventos DROP CONSTRAINT IF EXISTS alertas_eventos_origen_tipo_check;
ALTER TABLE alertas_eventos
  ADD CONSTRAINT alertas_eventos_origen_tipo_check
  CHECK (origen_tipo IN ('sensor', 'estacion', 'dispositivo_movil'));
COMMIT;
