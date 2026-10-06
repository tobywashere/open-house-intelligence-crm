class InstallError(RuntimeError):
    """An operator-facing failure whose message contains no private output."""
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)
