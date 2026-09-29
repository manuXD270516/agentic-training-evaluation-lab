class RunnerError(Exception):
    error_class = "infrastructure_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedPatternError(RunnerError):
    pass


class InvalidScriptError(RunnerError):
    error_class = "invalid_arguments"


class ModelNotAllowedError(RunnerError):
    pass


class ReplayNotImplementedError(RunnerError):
    pass


class TraceIntegrityError(RunnerError):
    error_class = "trace_error"
