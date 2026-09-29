from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "invalid_request"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class InvalidRequestError(DomainError):
    status_code = 422
    code = "invalid_request"


class InvalidReferenceError(DomainError):
    status_code = 422
    code = "invalid_reference"


class ExperimentSealedError(DomainError):
    status_code = 409
    code = "experiment_sealed"


class InvalidTransitionConflictError(DomainError):
    status_code = 409
    code = "invalid_transition"


class ExperimentNotSealedError(DomainError):
    status_code = 409
    code = "experiment_not_sealed"


class ExperimentNotAcceptingRunsError(DomainError):
    status_code = 409
    code = "experiment_not_accepting_runs"


class RunCellExistsError(DomainError):
    status_code = 409
    code = "run_cell_exists"


class IdempotencyKeyReusedError(DomainError):
    status_code = 409
    code = "idempotency_key_reused"


class HashMismatchError(DomainError):
    status_code = 422
    code = "hash_mismatch"


class VersionExistsError(DomainError):
    status_code = 409
    code = "version_exists"


class ContentDuplicateError(DomainError):
    status_code = 409
    code = "content_duplicate"
