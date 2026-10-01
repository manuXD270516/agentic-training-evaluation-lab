from collections.abc import Mapping

from evallab.runner.errors import ModelNotAllowedError
from evallab.runner.models import ModelRequest, ModelResult, ModelSnapshot


class DeniedModelGateway:
    """Scripted no llama modelos; un generate accidental no se atribuye a un LLM."""

    def models(self) -> Mapping[str, ModelSnapshot]:
        return {}

    def generate(self, request: ModelRequest) -> ModelResult:
        del request
        raise ModelNotAllowedError("el patrón scripted no llama a ModelGateway")
