"""ToolGateway sobre fixtures declarativas con estado aislado por run."""

from __future__ import annotations

import copy
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field, ValidationError, model_validator

from evallab.canonical import canonical_digest, canonical_json
from evallab.runner.contracts import AllowedTool, ToolOutcome
from evallab.schemas import JsonObject, JsonValue, StrictModel


class FixtureCase(StrictModel):
    arguments: JsonObject
    result: JsonValue
    state_patch: JsonObject | None = None


class FixtureDefault(StrictModel):
    result: JsonValue
    state_patch: JsonObject | None = None


class ToolFixture(StrictModel):
    kind: Literal["lookup"]
    cases: list[FixtureCase] = Field(default_factory=list)
    default: FixtureDefault | None = None

    @model_validator(mode="after")
    def _unique_cases(self) -> ToolFixture:
        keys = Counter(canonical_json(case.arguments) for case in self.cases)
        if any(count > 1 for count in keys.values()):
            raise ValueError("la fixture repite argumentos en varios casos")
        return self


@dataclass(frozen=True)
class ToolBinding:
    tool: AllowedTool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect_class: str
    fixture: Any | None


def _pointer(path: Iterable[Any]) -> str:
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in path]
    return "/" + "/".join(parts) if parts else ""


def _failed(error: str, *, validated: bool, reason: str) -> ToolOutcome:
    return ToolOutcome(
        kind="failed",
        validated=validated,
        reason_codes=(reason,),
        error_class="infrastructure_error",
        error=error,
    )


class FixtureToolGateway:
    """Un gateway por run: tools autorizadas, fixtures copiadas y estado propio.

    No tiene acceso a sistema de archivos, procesos ni red: sólo resuelve fixtures en memoria.
    """

    def __init__(
        self,
        allowed: Sequence[ToolBinding],
        *,
        scenario_only: Iterable[str] = (),
        agent_only: Iterable[str] = (),
        initial_state: dict[str, Any] | None = None,
    ) -> None:
        names = Counter(binding.tool.name for binding in allowed)
        self._ambiguous = {name for name, count in names.items() if count > 1}
        self._bindings = {
            b.tool.name: copy.deepcopy(b) for b in allowed if b.tool.name not in self._ambiguous
        }
        self._scenario_only = set(scenario_only)
        self._agent_only = set(agent_only)
        self._state: dict[str, Any] = copy.deepcopy(initial_state or {})
        self._validators: dict[str, Draft202012Validator] = {}
        self._fixtures: dict[str, ToolFixture] = {}

    def allowed_tools(self) -> Sequence[AllowedTool]:
        return tuple(binding.tool for binding in self._bindings.values())

    def resolve(self, name: str) -> AllowedTool | None:
        binding = self._bindings.get(name)
        return binding.tool if binding is not None else None

    @property
    def state(self) -> dict[str, Any]:
        return copy.deepcopy(self._state)

    def state_digest(self) -> str:
        return canonical_digest(self._state)

    def _denial(self, name: str) -> str:
        if name in self._ambiguous:
            return "ambiguous_tool"
        if name in self._scenario_only:
            return "not_in_agent"
        if name in self._agent_only:
            return "not_in_scenario"
        return "unknown_tool"

    def _validator(self, key: str, schema: dict[str, Any]) -> Draft202012Validator:
        validator = self._validators.get(key)
        if validator is None:
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
            self._validators[key] = validator
        return validator

    def _schema_errors(self, key: str, schema: dict[str, Any], value: Any) -> list[dict[str, str]]:
        validator = self._validator(key, schema)
        errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
        return [
            {"path": _pointer(error.absolute_path), "keyword": str(error.validator)}
            for error in errors
        ]

    def _fixture(self, binding: ToolBinding) -> ToolFixture:
        name = binding.tool.name
        parsed = self._fixtures.get(name)
        if parsed is None:
            parsed = ToolFixture.model_validate(binding.fixture)
            self._fixtures[name] = parsed
        return parsed

    def invoke(self, name: str, call_id: uuid.UUID, arguments: dict[str, Any]) -> ToolOutcome:
        del call_id
        binding = self._bindings.get(name)
        if binding is None:
            return ToolOutcome(kind="denied", validated=False, reason_codes=(self._denial(name),))

        try:
            errors = self._schema_errors(f"{name}:input", binding.input_schema, arguments)
        except SchemaError:
            return _failed("input_schema inválido", validated=False, reason="invalid_input_schema")
        except Exception as exc:
            return _failed(
                f"no se pudo validar argumentos: {type(exc).__name__}",
                validated=False,
                reason="input_schema_unresolvable",
            )
        if errors:
            return ToolOutcome(
                kind="invalid",
                validated=False,
                reason_codes=("schema_invalid",),
                schema_errors=tuple(errors),
                error_class="invalid_arguments",
                error="argumentos no cumplen input_schema",
            )

        if binding.fixture is None:
            return _failed("tool sin fixture", validated=True, reason="fixture_missing")
        try:
            fixture = self._fixture(binding)
        except ValidationError:
            return _failed("fixture de tool inválida", validated=True, reason="fixture_invalid")

        key = canonical_json(arguments)
        case: FixtureCase | FixtureDefault | None = next(
            (c for c in fixture.cases if canonical_json(c.arguments) == key), fixture.default
        )
        if case is None:
            return _failed("la fixture no cubre estos argumentos", validated=True, reason="no_case")
        if case.state_patch is not None and binding.effect_class != "side_effect":
            return _failed(
                "state_patch en tool read_only", validated=True, reason="effect_class_violation"
            )

        try:
            output_errors = self._schema_errors(
                f"{name}:output", binding.output_schema, case.result
            )
        except SchemaError:
            return _failed("output_schema inválido", validated=True, reason="invalid_output_schema")
        except Exception as exc:
            return _failed(
                f"no se pudo validar resultado: {type(exc).__name__}",
                validated=True,
                reason="output_schema_unresolvable",
            )
        if output_errors:
            return _failed(
                "el resultado de la fixture no cumple output_schema",
                validated=True,
                reason="output_schema_invalid",
            )

        if case.state_patch is not None:
            self._state.update(copy.deepcopy(case.state_patch))
        return ToolOutcome(
            kind="completed",
            validated=True,
            result=copy.deepcopy(case.result),
            state_digest=self.state_digest(),
        )
