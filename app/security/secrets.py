"""Envelope for HMAC secrets encrypted at rest with a deployment master key."""

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    def __init__(self, master_key: str):
        self._fernet = Fernet(master_key.encode("ascii"))

    def encrypt(self, secret: str) -> bytes:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("La clave del dispositivo debe tener al menos 32 bytes")
        return self._fernet.encrypt(secret.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> bytes:
        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise ValueError("No se pudo descifrar la credencial") from exc

