import os

from cryptography.fernet import Fernet

os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "quinuaalert_test")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", "test-password")
os.environ.setdefault("HMAC_MASTER_KEY", Fernet.generate_key().decode())

