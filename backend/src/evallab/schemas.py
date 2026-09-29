import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from evallab.db.models import SEMVER

Version = Annotated[str, Field(pattern=SEMVER)]
Seed = Annotated[int, Field(ge=0, le=2**63 - 1)]
JsonObject = dict[str, JsonValue]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionRef(StrictModel):
    id: uuid.UUID
    version: Version


def _unique_refs(refs: list[VersionRef] | None) -> None:
    if refs is not None and len({(r.id, r.version) for r in refs}) != len(refs):
        raise ValueError("agents contiene referencias duplicadas")


class ExperimentCreate(StrictModel):
    hypothesis: str = Field(min_length=1)
    benchmark: VersionRef | None = None
    agents: list[VersionRef] = Field(default_factory=list)
    budgets: JsonObject = Field(default_factory=dict)
    repetitions: int = Field(ge=1, le=1000)
    seeds: list[Seed]
    comparison_plan: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len(self.seeds) != self.repetitions:
            raise ValueError("seeds debe tener una seed por repetición")
        _unique_refs(self.agents)
        return self


class ExperimentUpdate(StrictModel):
    """Campos editables de un draft; los ausentes no cambian."""

    hypothesis: str | None = Field(default=None, min_length=1)
    benchmark: VersionRef | None = None
    agents: list[VersionRef] | None = None
    budgets: JsonObject | None = None
    repetitions: int | None = Field(default=None, ge=1, le=1000)
    seeds: list[Seed] | None = None
    comparison_plan: JsonObject | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _unique_refs(self.agents)
        return self


class ExperimentOut(BaseModel):
    id: uuid.UUID
    status: str
    hypothesis: str
    benchmark: VersionRef | None
    agents: list[VersionRef]
    budgets: JsonObject
    repetitions: int
    seeds: list[int]
    comparison_plan: JsonObject
    manifest_hash: str | None
    created_at: datetime
    sealed_at: datetime | None


class ManifestOut(BaseModel):
    experiment_id: uuid.UUID
    manifest_hash: str
    manifest: JsonObject


class RunCreate(StrictModel):
    scenario: VersionRef
    agent: VersionRef
    repetition: int = Field(ge=1)
    mode: Literal["live", "replay"]


class RunOut(BaseModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    scenario: VersionRef
    agent: VersionRef
    repetition: int
    seed: int
    mode: str
    status: str
    error_class: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
