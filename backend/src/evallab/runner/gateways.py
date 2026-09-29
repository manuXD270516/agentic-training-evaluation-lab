from collections.abc import Sequence
from typing import Any

from evallab.runner.contracts import AllowedTool
from evallab.runner.errors import ModelNotAllowedError


class DeniedModelGateway:
    """Scripted no llama modelos; un generate accidental no se atribuye a un LLM."""

    def generate(self, messages: Sequence[Any], tools: Sequence[AllowedTool]) -> Any:
        del messages, tools
        raise ModelNotAllowedError("el patrón scripted no llama a ModelGateway")
