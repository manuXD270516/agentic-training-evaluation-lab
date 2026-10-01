"""ToolGateway sobre fixtures declarativas con estado aislado por run."""

from __future__ import annotations

import copy
import dataclasses
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

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


class FaultSpec(StrictModel):
    """Fallo inyectado en la n-ésima ejecución de una tool dentro del run (retries incluidos)."""

    fault_id: str = Field(min_length=1, max_length=128)
    tool: str = Field(min_length=1)
    call_index: int = Field(ge=1)
    kind: Literal["timeout", "transient"]
    effect_applied: bool = False

    @model_validator(mode="after")
    def _effect_only_on_timeout(self) -> FaultSpec:
        if self.effect_applied and self.kind != "timeout":
            raise ValueError("effect_applied sólo tiene sentido en un timeout")
        return self


class FaultSchedule(StrictModel):
    faults: list[FaultSpec]

    @model_validator(mode="after")
    def _unique(self) -> FaultSchedule:
        keys = Counter((f.tool, f.call_index) for f in self.faults)
        if any(count > 1 for count in keys.values()):
            raise ValueError("fault_schedule repite (tool, call_index)")
        if len({f.fault_id for f in self.faults}) != len(self.faults):
            raise ValueError("fault_schedule repite fault_id")
        return self


def parse_fault_schedule(raw: Any, known_tools: Iterable[str]) -> tuple[FaultSpec, ...]:
    """Valida el fault_schedule privado; ValueError si es inválido o nombra tools ajenas."""
    if raw is None:
        return ()
    try:
        schedule = FaultSchedule.model_validate({"faults": raw})
    except ValidationError as exc:
        raise ValueError("fault_schedule inválido") from exc
    unknown = sorted({f.tool for f in schedule.faults} - set(known_tools))
    if unknown:
        raise ValueError(f"fault_schedule nombra tools fuera del escenario: {unknown}")
    return tuple(schedule.faults)


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


class RetrieverFixture(StrictModel):
    """Tool de recuperación sobre un retriever versionado (M9): `{query}` → chunks rankeados."""

    kind: Literal["retriever"]
    retriever_ref: str = Field(pattern=r"^[0-9a-f]{64}$")


class RetrievalBackend(Protocol):
    def search(self, retriever_hash: str, query: str) -> tuple[Any, Sequence[Any]]: ...


@dataclass(frozen=True)
class ToolBinding:
    tool: AllowedTool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect_class: str
    fixture: Any | None
    timeout_ms: int | None = None


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
    El `call_id` es la clave de idempotencia: una tool con efectos ya aplicados para esa
    clave devuelve el mismo resultado sin volver a aplicar el efecto.
    """

    def __init__(
        self,
        allowed: Sequence[ToolBinding],
        *,
        scenario_only: Iterable[str] = (),
        agent_only: Iterable[str] = (),
        initial_state: dict[str, Any] | None = None,
        faults: Sequence[FaultSpec] = (),
        retrieval: RetrievalBackend | None = None,
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
        self._fixtures: dict[str, ToolFixture | RetrieverFixture] = {}
        self._faults = {(f.tool, f.call_index): f for f in faults}
        self._executions: Counter[str] = Counter()
        self._applied: dict[uuid.UUID, Any] = {}
        self._retrieval = retrieval

    def allowed_tools(self) -> Sequence[AllowedTool]:
        return tuple(
            dataclasses.replace(binding.tool, input_schema=binding.input_schema)
            for binding in self._bindings.values()
        )

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

    def _fixture(self, binding: ToolBinding) -> ToolFixture | RetrieverFixture:
        name = binding.tool.name
        parsed = self._fixtures.get(name)
        if parsed is None:
            raw = binding.fixture
            if isinstance(raw, dict) and raw.get("kind") == "retriever":
                parsed = RetrieverFixture.model_validate(raw)
            else:
                parsed = ToolFixture.model_validate(raw)
            self._fixtures[name] = parsed
        return parsed

    def _retrieve(
        self, binding: ToolBinding, fixture: RetrieverFixture, arguments: dict[str, Any]
    ) -> ToolOutcome:
        query = arguments.get("query")
        if self._retrieval is None or not isinstance(query, str):
            return _failed("recuperación no disponible", validated=True, reason="no_retriever")
        try:
            config, ranked = self._retrieval.search(fixture.retriever_ref, query)
        except LookupError:
            return _failed("retriever inexistente", validated=True, reason="retriever_missing")
        result = {
            "results": [
                {"chunk_id": r.chunk_id, "doc_id": r.doc_id, "text": r.text, "score": r.score}
                for r in ranked
            ]
        }
        try:
            output_errors = self._schema_errors(
                f"{binding.tool.name}:output", binding.output_schema, result
            )
        except SchemaError:
            return _failed("output_schema inválido", validated=True, reason="invalid_output_schema")
        if output_errors:
            return _failed(
                "el resultado no cumple output_schema",
                validated=True,
                reason="output_schema_invalid",
            )
        self._executions[binding.tool.name] += 1
        return ToolOutcome(
            kind="completed",
            validated=True,
            result=result,
            state_digest=self.state_digest(),
            retrieval={
                "query": query,
                "retriever_ref": fixture.retriever_ref,
                "embedding_set_ref": config.embedding_set,
                "distance": config.distance,
                "index_kind": config.index_kind,
                "tie_break": config.tie_break,
                "top_k": config.top_k,
                "ranked_chunk_ids": [r.chunk_id for r in ranked],
                "scores": [r.score for r in ranked],
            },
        )

    def invoke(self, name: str, call_id: uuid.UUID, arguments: dict[str, Any]) -> ToolOutcome:
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
        if isinstance(fixture, RetrieverFixture):
            return self._retrieve(binding, fixture, arguments)

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

        if call_id in self._applied:
            return ToolOutcome(
                kind="completed",
                validated=True,
                result=copy.deepcopy(self._applied[call_id]),
                state_digest=self.state_digest(),
                idempotent_replay=True,
            )

        self._executions[name] += 1
        fault = self._faults.get((name, self._executions[name]))
        side_effect = binding.effect_class == "side_effect"
        if fault is not None and fault.kind == "transient":
            return ToolOutcome(
                kind="failed",
                validated=True,
                reason_codes=("transient_fault",),
                error_class="transient_tool_error",
                error="fallo transitorio inyectado",
                retriable=True,
                fault_id=fault.fault_id,
            )
        if fault is not None and fault.kind == "timeout":
            if side_effect and fault.effect_applied:
                self._apply(call_id, case)
            return ToolOutcome(
                kind="failed",
                validated=True,
                reason_codes=("ambiguous_effect",) if side_effect else ("timeout",),
                error_class="tool_timeout",
                error="la tool superó su timeout",
                retriable=not side_effect,
                ambiguous_effect=side_effect,
                fault_id=fault.fault_id,
                timeout_ms=binding.timeout_ms,
            )

        if side_effect:
            self._apply(call_id, case)
        return ToolOutcome(
            kind="completed",
            validated=True,
            result=copy.deepcopy(case.result),
            state_digest=self.state_digest(),
        )

    def _apply(self, call_id: uuid.UUID, case: FixtureCase | FixtureDefault) -> None:
        if case.state_patch is not None:
            self._state.update(copy.deepcopy(case.state_patch))
        self._applied[call_id] = copy.deepcopy(case.result)
