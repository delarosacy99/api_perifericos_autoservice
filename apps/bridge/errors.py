class BridgeError(Exception):
    def __init__(self, code, message, status=409):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class CentralError(BridgeError):
    def __init__(self, code, status=503, *, uncertain=False):
        self.uncertain = uncertain
        super().__init__(
            code, "La API central no confirmó la operación. Consulte su estado.", status
        )
