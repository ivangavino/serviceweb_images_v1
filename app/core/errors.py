"""Public API errors with stable machine-readable codes."""


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class RecoverableStorageError(ApiError):
    def __init__(self, message: str = "La carga quedó pendiente de recuperación"):
        super().__init__(503, "storage_recovery_pending", message)

