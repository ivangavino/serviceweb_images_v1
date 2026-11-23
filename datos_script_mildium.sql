-- QuinuAlert - datos iniciales requeridos por el servicio web de mildiu
--
-- Este script se ejecuta DESPUES de script.sql. Registra:
--   1. un prototipo autorizado para enviar solicitudes;
--   2. su credencial HMAC cifrada con la HMAC_MASTER_KEY de la API;
--   3. la autorizacion del prototipo sobre un terreno existente y activo.
--
-- No inserta tomas, surcos, resultados ni imagenes: esos registros son
-- creados por la API cuando recibe las solicitudes del prototipo.
--
-- Compatible con pgAdmin Query Tool y con psql: todo el contenido ejecutable
-- es SQL de PostgreSQL, sin metacomandos que comiencen con barra invertida.
--
-- Antes de ejecutarlo, genere el secreto cifrado desde PowerShell (el secreto
-- en texto plano nunca se guarda en este archivo):
--
--   $env:DEVICE_HMAC_SECRET = '<secreto-aleatorio-de-al-menos-32-bytes>'
--   .\.venv\Scripts\python.exe -c "import os; from app.core.config import get_settings; from app.security.secrets import SecretCipher; print(SecretCipher(get_settings().hmac_master_key.get_secret_value()).encrypt(os.environ['DEVICE_HMAC_SECRET']).hex())"
--   Remove-Item Env:DEVICE_HMAC_SECRET
--
-- Copie el valor hexadecimal impreso y peguelo en secreto_cifrado_hex dentro
-- del bloque CONFIGURACION que aparece mas abajo. Debe haberse generado con la
-- misma HMAC_MASTER_KEY que utiliza la API.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '1min';
SET LOCAL search_path = public;

DO $preflight$
DECLARE
    v_tabla text;
BEGIN
    FOREACH v_tabla IN ARRAY ARRAY[
        'terrenos',
        'prototipos_mildiu',
        'credenciales_prototipo_mildiu',
        'prototipos_terrenos_mildiu'
    ]
    LOOP
        IF to_regclass(format('public.%I', v_tabla)) IS NULL THEN
            RAISE EXCEPTION
                'No existe public.%. Ejecute primero el esquema base y script.sql.',
                v_tabla;
        END IF;
    END LOOP;
END
$preflight$;

-- CONFIGURACION: edite solamente los valores de este SELECT.
-- secreto_cifrado_hex no puede quedar vacio.
-- La tabla temporal se elimina automaticamente al confirmar la transaccion.
CREATE TEMPORARY TABLE _config_datos_mildium ON COMMIT DROP AS
SELECT
    1::bigint AS terreno_id,
    'PROT-PUEMAPE-001'::text AS prototipo_codigo,
    'Raspberry Puemape 1'::text AS prototipo_nombre,
    'Prototipo para captura de mildiu'::text AS prototipo_descripcion,
    'key-001'::text AS key_id,
    decode(
        '674141414141427242466e754663534d34555452627867324e717a5a7562672d4938346c65427a545134726d546871636a7053496154733377506f41625a6b4e586a716763764b5978742d357746396d564d38574c366e6c586d444d4f3876707a672d6f467861745f624d5169507733784b58496d55774b683666545467386c4e58706b4f55517037493646'::text,
        'hex'
    ) AS secreto_cifrado;

DO $aprovisionamiento$
DECLARE
    v_config record;
    v_prototipo_id bigint;
    v_estado_prototipo varchar(20);
    v_estado_credencial varchar(20);
BEGIN
    SELECT *
      INTO STRICT v_config
      FROM _config_datos_mildium;

    IF v_config.terreno_id <= 0 THEN
        RAISE EXCEPTION 'terreno_id debe ser un entero positivo.';
    END IF;

    IF v_config.prototipo_codigo !~ '^[A-Za-z0-9_.:-]{1,100}$' THEN
        RAISE EXCEPTION
            'prototipo_codigo solo admite letras ASCII, numeros y los caracteres _ . : - (maximo 100).';
    END IF;

    IF btrim(v_config.prototipo_nombre) = ''
       OR char_length(v_config.prototipo_nombre) > 150 THEN
        RAISE EXCEPTION
            'prototipo_nombre debe contener entre 1 y 150 caracteres.';
    END IF;

    IF v_config.key_id !~ '^[A-Za-z0-9_.:-]{1,100}$' THEN
        RAISE EXCEPTION
            'key_id solo admite letras ASCII, numeros y los caracteres _ . : - (maximo 100).';
    END IF;

    -- SecretCipher almacena un token Fernet codificado como ASCII. Esta
    -- comprobacion evita insertar por accidente texto plano o un valor vacio.
    IF octet_length(v_config.secreto_cifrado) < 100
       OR encode(v_config.secreto_cifrado, 'escape')
          !~ '^gAAAA[A-Za-z0-9_-]+={0,2}$' THEN
        RAISE EXCEPTION
            'secreto_cifrado_hex no contiene un token Fernet valido. Genere el valor con SecretCipher y la HMAC_MASTER_KEY de la API.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM public.terrenos AS t
         WHERE t.terreno_id = v_config.terreno_id
           AND t.activo IS TRUE
    ) THEN
        RAISE EXCEPTION
            'El terreno % no existe o esta inactivo.',
            v_config.terreno_id;
    END IF;

    INSERT INTO public.prototipos_mildiu (
        codigo,
        nombre,
        descripcion,
        estado
    )
    VALUES (
        v_config.prototipo_codigo,
        v_config.prototipo_nombre,
        v_config.prototipo_descripcion,
        'activo'
    )
    ON CONFLICT (codigo) DO NOTHING
    RETURNING prototipo_id, estado
         INTO v_prototipo_id, v_estado_prototipo;

    IF v_prototipo_id IS NULL THEN
        SELECT p.prototipo_id, p.estado
          INTO STRICT v_prototipo_id, v_estado_prototipo
          FROM public.prototipos_mildiu AS p
         WHERE p.codigo = v_config.prototipo_codigo
         FOR UPDATE;
    END IF;

    IF v_estado_prototipo <> 'activo' THEN
        RAISE EXCEPTION
            'El prototipo % ya existe con estado %. No se reactivo automaticamente.',
            v_config.prototipo_codigo,
            v_estado_prototipo;
    END IF;

    SELECT c.estado
      INTO v_estado_credencial
      FROM public.credenciales_prototipo_mildiu AS c
     WHERE c.prototipo_id = v_prototipo_id
       AND c.key_id = v_config.key_id
     FOR UPDATE;

    IF FOUND THEN
        IF v_estado_credencial <> 'activa' THEN
            RAISE EXCEPTION
                'La credencial % ya existe con estado %. Use manage_prototype.py rotate para crear otra clave.',
                v_config.key_id,
                v_estado_credencial;
        END IF;

        -- Fernet genera un cifrado distinto aun para el mismo texto plano. En
        -- una repeticion conservamos la credencial activa existente; nunca se
        -- reemplaza silenciosamente una clave. Para cambiarla use rotacion.
        RAISE NOTICE
            'La credencial activa % ya existe; se conserva sin cambios.',
            v_config.key_id;
    ELSE
        INSERT INTO public.credenciales_prototipo_mildiu (
            prototipo_id,
            key_id,
            secreto_cifrado,
            estado
        )
        VALUES (
            v_prototipo_id,
            v_config.key_id,
            v_config.secreto_cifrado,
            'activa'
        );
    END IF;

    INSERT INTO public.prototipos_terrenos_mildiu (
        prototipo_id,
        terreno_id,
        activo
    )
    VALUES (
        v_prototipo_id,
        v_config.terreno_id,
        true
    )
    ON CONFLICT (prototipo_id, terreno_id)
    DO UPDATE SET activo = true;
END
$aprovisionamiento$;

-- Resumen sin exponer el secreto cifrado.
SELECT
    p.prototipo_id,
    p.codigo AS prototipo_codigo,
    p.nombre AS prototipo_nombre,
    p.estado AS prototipo_estado,
    c.key_id,
    c.estado AS credencial_estado,
    pt.terreno_id,
    pt.activo AS terreno_autorizado
FROM public.prototipos_mildiu AS p
JOIN public.credenciales_prototipo_mildiu AS c
  ON c.prototipo_id = p.prototipo_id
JOIN public.prototipos_terrenos_mildiu AS pt
  ON pt.prototipo_id = p.prototipo_id
JOIN _config_datos_mildium AS cfg
  ON cfg.prototipo_codigo = p.codigo
 AND cfg.key_id = c.key_id
 AND cfg.terreno_id = pt.terreno_id;

COMMIT;
