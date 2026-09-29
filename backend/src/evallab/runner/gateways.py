from collections.abc import Sequence
from typing import Any
from uuid import UUID

from evallab.runner.contracts import AllowedTool
from evallab.runner.errors import ModelNotAllowedError


class DeniedModelGateway:
    """Scripted no llama modelos; un generate accidental no se atribuye a un LLM."""

    def generate(self, messages: Sequence[Any], tools: Sequence[AllowedTool]) -> Any:
        del messages, tools
        raise ModelNotAllowedError("el patrón scripted no llama a ModelGateway")


class DeniedToolGateway:
    """Las observaciones scripted las aporta el script; el gateway real llega en 3.2."""

    def invoke(self, tool: str, version: str, call_id: UUID, arguments: dict[str, Any]) -> Any:
        del tool, version, call_id, arguments
        raise ModelNotAllowedError("ToolGateway no está implementado; usar result del script")
