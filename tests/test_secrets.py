import pytest
from cryptography.fernet import Fernet

from app.security.secrets import SecretCipher


def test_device_secret_is_encrypted_and_decrypted():
    cipher = SecretCipher(Fernet.generate_key().decode())
    secret = "a-unique-device-secret-with-more-than-32-bytes"
    encrypted = cipher.encrypt(secret)
    assert secret.encode() not in encrypted
    assert cipher.decrypt(encrypted) == secret.encode()


def test_rejects_weak_device_secret():
    cipher = SecretCipher(Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        cipher.encrypt("too-short")
