class UserError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ServiceError(RuntimeError):
    """Safe customer message plus a stable operator diagnostic; never raw HTTP bodies."""

    def __init__(self, code, message, http_status=503):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
