"""Publicación inmutable de ModelConfiguration y AgentConfiguration (snapshot completo).

El hash de contenido de un agente cubre patrón, versión, prompt, parámetros, roles con el hash
de su modelo y tools con su hash: cambiar cualquiera exige otra versión.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evallab.db import models as m
from evallab.schemas import (
    AgentConfigurationCreate,
    AgentConfigurationOut,
    ModelConfigurationCreate,
    ModelConfigurationOut,
    VersionRef,
)
from evallab.services.catalog import (
    _check_client_hash,
    _content_duplicate,
    _digest,
    _resolve_tools,
    _version_exists,
)
from evallab.services.errors import InvalidReferenceError, NotFoundError


def _temperature(value: float | None) -> str | None:
    # Decimal desde str: el manifest serializa decimales como cadenas sin pérdida.
    return None if value is None else format(Decimal(str(value)).normalize(), "f")


def model_document(data: ModelConfigurationCreate, model_id: uuid.UUID) -> dict[str, Any]:
    return {
        "id": str(model_id),
        "version": data.version,
        "provider": data.provider,
        "requested_model": data.requested_model,
        "resolved_revision": data.resolved_revision,
        "temperature": _temperature(data.temperature),
        "seed_support": data.seed_support,
        "max_tokens": data.max_tokens,
        "price_snapshot_ref": data.price_snapshot_ref,
    }


def publish_model(db: Session, data: ModelConfigurationCreate) -> m.ModelConfiguration:
    model_id = data.id or uuid.uuid4()
    document = model_document(data, model_id)
    content_hash = _digest(document)
    _check_client_hash(content_hash, data.content_hash)
    existing = db.get(m.ModelConfiguration, (model_id, data.version))
    if existing is not None:
        _version_exists(
            "model configuration",
            VersionRef(id=model_id, version=data.version),
            content_hash,
            existing.content_hash,
        )
    twin = db.scalar(
        select(m.ModelConfiguration).where(m.ModelConfiguration.content_hash == content_hash)
    )
    if twin is not None:
        _content_duplicate("model configuration", twin)
    temperature = document["temperature"]
    row = m.ModelConfiguration(
        id=model_id,
        version=data.version,
        provider=data.provider,
        requested_model=data.requested_model,
        resolved_revision=data.resolved_revision,
        temperature=None if temperature is None else Decimal(temperature),
        seed_support=data.seed_support,
        max_tokens=data.max_tokens,
        price_snapshot_ref=data.price_snapshot_ref,
        content_hash=content_hash,
    )
    db.add(row)
    db.flush()
    return row


def get_model(db: Session, model_id: uuid.UUID, version: str) -> m.ModelConfiguration:
    row = db.get(m.ModelConfiguration, (model_id, version))
    if row is None:
        raise NotFoundError(
            "configuración de modelo inexistente", id=str(model_id), version=version
        )
    return row


def model_to_out(row: m.ModelConfiguration) -> ModelConfigurationOut:
    return ModelConfigurationOut(
        id=row.id,
        version=row.version,
        provider=row.provider,
        requested_model=row.requested_model,
        resolved_revision=row.resolved_revision,
        temperature=None if row.temperature is None else format(row.temperature, "f"),
        seed_support=row.seed_support,
        max_tokens=row.max_tokens,
        price_snapshot_ref=row.price_snapshot_ref,
        content_hash=row.content_hash,
        created_at=row.created_at,
    )


def agent_document(
    data: AgentConfigurationCreate,
    agent_id: uuid.UUID,
    models: dict[str, m.ModelConfiguration],
    tools: list[m.ToolDefinition],
) -> dict[str, Any]:
    return {
        "id": str(agent_id),
        "version": data.version,
        "pattern": data.pattern,
        "pattern_version": data.pattern_version,
        "prompt_hash": data.prompt_hash,
        "pattern_parameters": data.pattern_parameters,
        "roles": [
            {
                "role": role,
                "model": {
                    "id": str(models[role].id),
                    "version": models[role].version,
                    "content_hash": models[role].content_hash,
                },
            }
            for role in sorted(models)
        ],
        "tools": sorted(
            (
                {"id": str(t.id), "version": t.version, "content_hash": t.content_hash}
                for t in tools
            ),
            key=lambda ref: (ref["id"], ref["version"]),
        ),
    }


def publish_agent(db: Session, data: AgentConfigurationCreate) -> m.AgentConfiguration:
    models: dict[str, m.ModelConfiguration] = {}
    for role in data.roles:
        model = db.get(m.ModelConfiguration, (role.model.id, role.model.version))
        if model is None:
            raise InvalidReferenceError(
                "configuración de modelo inexistente", role=role.model_dump(mode="json")
            )
        models[role.role] = model
    tools = _resolve_tools(db, data.tools)
    agent_id = data.id or uuid.uuid4()
    document = agent_document(data, agent_id, models, tools)
    content_hash = _digest(document)
    _check_client_hash(content_hash, data.content_hash)
    existing = db.get(m.AgentConfiguration, (agent_id, data.version))
    if existing is not None:
        _version_exists(
            "agent configuration",
            VersionRef(id=agent_id, version=data.version),
            content_hash,
            existing.content_hash,
        )
    twin = db.scalar(
        select(m.AgentConfiguration).where(m.AgentConfiguration.content_hash == content_hash)
    )
    if twin is not None:
        _content_duplicate("agent configuration", twin)
    row = m.AgentConfiguration(
        id=agent_id,
        version=data.version,
        pattern=data.pattern,
        pattern_version=data.pattern_version,
        prompt_hash=data.prompt_hash,
        pattern_parameters=data.pattern_parameters,
        content_hash=content_hash,
    )
    db.add(row)
    db.flush()
    db.add_all(
        m.AgentRole(
            agent_id=row.id,
            agent_version=row.version,
            role=role,
            model_id=model.id,
            model_version=model.version,
        )
        for role, model in models.items()
    )
    db.add_all(
        m.AgentTool(
            agent_id=row.id, agent_version=row.version, tool_id=t.id, tool_version=t.version
        )
        for t in tools
    )
    db.flush()
    return row


def get_agent(db: Session, agent_id: uuid.UUID, version: str) -> m.AgentConfiguration:
    row = db.get(m.AgentConfiguration, (agent_id, version))
    if row is None:
        raise NotFoundError(
            "configuración de agente inexistente", id=str(agent_id), version=version
        )
    return row


def agent_to_out(db: Session, row: m.AgentConfiguration) -> AgentConfigurationOut:
    roles = db.scalars(
        select(m.AgentRole)
        .where(m.AgentRole.agent_id == row.id, m.AgentRole.agent_version == row.version)
        .order_by(m.AgentRole.role)
    )
    links = db.scalars(
        select(m.AgentTool)
        .where(m.AgentTool.agent_id == row.id, m.AgentTool.agent_version == row.version)
        .order_by(m.AgentTool.tool_id)
    )
    tools = []
    for link in links:
        tool = db.get(m.ToolDefinition, (link.tool_id, link.tool_version))
        if tool is not None:
            tools.append(
                {
                    "id": str(tool.id),
                    "version": tool.version,
                    "name": tool.name,
                    "content_hash": tool.content_hash,
                }
            )
    return AgentConfigurationOut(
        id=row.id,
        version=row.version,
        pattern=row.pattern,
        pattern_version=row.pattern_version,
        prompt_hash=row.prompt_hash,
        pattern_parameters=row.pattern_parameters,
        roles=[
            {"role": r.role, "model": {"id": str(r.model_id), "version": r.model_version}}
            for r in roles
        ],
        tools=tools,
        content_hash=row.content_hash,
        created_at=row.created_at,
    )
