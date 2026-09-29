import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.domain.vocabulary import PRIMARY_CATEGORIES
from evallab.schemas import ScenarioCreate
from evallab.services.catalog import HASH_EXCLUDE, scenario_document

pytestmark = pytest.mark.integration


@pytest.fixture
def client(migrated_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=migrated_database)) as test_client:
        yield test_client


def _key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def _count(engine: Engine, model: type[m.Base], *where: Any) -> int:
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _post(
    client: TestClient, path: str, body: dict[str, Any], headers: dict[str, str] | None = None
) -> Response:
    return client.post(path, json=body, headers=headers or _key())


def _fixture(client: TestClient, name: str = "calculator") -> str:
    response = _post(client, "/fixtures", {"name": name, "payload": {"op": "add"}})
    assert response.status_code == 201, response.text
    return str(response.json()["content_hash"])


def _tool(client: TestClient, fixture_hash: str) -> dict[str, str]:
    response = _post(
        client,
        "/tool-definitions",
        {
            "version": "1.0.0",
            "name": f"calculator-{uuid.uuid4().hex[:8]}",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "effect_class": "read_only",
            "timeout_ms": 1000,
            "fixture_hash": fixture_hash,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return {"id": body["id"], "version": body["version"], "content_hash": body["content_hash"]}


def _scenario_body(
    tool: dict[str, str],
    fixture_hash: str,
    *,
    category: str = "reasoning",
    split: str = "dev",
    family: str | None = None,
    expected_value: int = 42,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version": "1.0.0",
        "slug": f"case-{uuid.uuid4().hex[:12]}",
        "primary_category": category,
        "difficulty": "easy",
        "split": split,
        "family_id": family or uuid.uuid4().hex,
        "task": {"instruction": "Suma 17 y 25", "input": {"a": 17, "b": 25}},
        "tools": [tool],
        "environment": {"fixture_refs": [fixture_hash], "fault_schedule": {"step": 2}},
        "limits": {
            "max_steps": 4,
            "max_model_calls": 4,
            "max_tool_calls": 2,
            "max_tokens": 2000,
            "deadline_ms": 30000,
            "max_retries": 0,
        },
        "expected": {
            "output_schema": {"type": "object"},
            "checks": [
                {"operator": "json_value_equals", "path": "/total", "value": expected_value},
                {"operator": "required_tool", "tool": "placeholder", "min_calls": 1},
            ],
        },
        "evaluation": {
            "required_checks": ["outcome", "output_structure", "required_tool"],
            "applicable_metrics": ["task_success", "tool_accuracy"],
        },
    }
    if category == "retrieval":
        body["retrieval"] = {
            "corpus_ref": fixture_hash,
            "top_k": 5,
            "qrels": {"q1": {"chunk-1": 1}},
        }
        body["evaluation"]["required_checks"] = ["outcome", "retrieval"]
        body["evaluation"]["applicable_metrics"] = ["task_success", "retrieval_recall_at_k"]
    return body


def _publish_scenario(
    client: TestClient, tool: dict[str, str], fixture_hash: str, **overrides: Any
) -> dict[str, Any]:
    name = client.get(f"/tool-definitions/{tool['id']}/versions/{tool['version']}").json()["name"]
    body = _scenario_body(tool, fixture_hash, **overrides)
    body["expected"]["checks"][1]["tool"] = name
    response = _post(client, "/scenarios", body)
    assert response.status_code == 201, response.text
    published: dict[str, Any] = response.json()
    return published


def _catalog(client: TestClient) -> tuple[str, dict[str, str]]:
    fixture_hash = _fixture(client)
    return fixture_hash, _tool(client, fixture_hash)


def test_fixture_is_content_addressed(client: TestClient, migrated_database: Engine) -> None:
    first = _post(client, "/fixtures", {"name": "clock", "payload": {"t": 0}})
    second = _post(client, "/fixtures", {"name": "clock", "payload": {"t": 0}})
    assert first.status_code == second.status_code == 201
    assert first.json()["content_hash"] == second.json()["content_hash"]
    assert _count(migrated_database, m.Fixture) == 1


def test_tool_rejects_missing_fixture(client: TestClient, migrated_database: Engine) -> None:
    missing = canonical_digest({"missing": True})
    response = _post(
        client,
        "/tool-definitions",
        {
            "version": "1.0.0",
            "name": "ghost",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "effect_class": "read_only",
            "timeout_ms": 1,
            "fixture_hash": missing,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"
    assert _count(migrated_database, m.ToolDefinition, m.ToolDefinition.name == "ghost") == 0


def test_scenario_without_oracle_checks_is_rejected(
    client: TestClient, migrated_database: Engine
) -> None:
    fixture_hash, tool = _catalog(client)
    body = _scenario_body(tool, fixture_hash)
    body["expected"]["checks"] = []
    response = _post(client, "/scenarios", body)
    assert response.status_code == 422
    assert _count(migrated_database, m.Scenario, m.Scenario.slug == body["slug"]) == 0


def test_scenario_with_missing_fixture_is_not_published(
    client: TestClient, migrated_database: Engine
) -> None:
    fixture_hash, tool = _catalog(client)
    body = _scenario_body(tool, fixture_hash)
    name = client.get(f"/tool-definitions/{tool['id']}/versions/{tool['version']}").json()["name"]
    body["expected"]["checks"][1]["tool"] = name
    body["environment"]["fixture_refs"] = [canonical_digest({"no": True})]
    response = _post(client, "/scenarios", body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"
    assert _count(migrated_database, m.Scenario, m.Scenario.slug == body["slug"]) == 0


def test_retrieval_qrels_are_not_in_public_view(client: TestClient) -> None:
    fixture_hash, tool = _catalog(client)
    published = _publish_scenario(client, tool, fixture_hash, category="retrieval")
    url = f"/scenarios/{published['id']}/versions/{published['version']}"
    public = client.get(url).json()
    oracle = client.get(f"{url}/oracle").json()
    assert "qrels" not in str(public)
    assert "retrieval" not in public
    assert oracle["retrieval"]["qrels"]["q1"]["chunk-1"] == 1


def test_public_view_hides_oracle_and_private_labels(client: TestClient) -> None:
    fixture_hash, tool = _catalog(client)
    published = _publish_scenario(client, tool, fixture_hash)
    public = client.get(f"/scenarios/{published['id']}/versions/{published['version']}")
    oracle = client.get(f"/scenarios/{published['id']}/versions/{published['version']}/oracle")
    assert public.status_code == oracle.status_code == 200
    body = public.json()
    assert "expected" not in body
    assert "fault_schedule" not in body["environment"]
    assert "split" not in body
    assert "family_id" not in body
    assert oracle.json()["expected"]["checks"][0]["value"] == 42
    assert oracle.json()["split"] == "dev"
    assert "fault_schedule" in oracle.json()["environment"]


def test_changing_oracle_requires_new_version(
    client: TestClient, migrated_database: Engine
) -> None:
    fixture_hash, tool = _catalog(client)
    first = _publish_scenario(client, tool, fixture_hash, expected_value=42)
    name = client.get(f"/tool-definitions/{tool['id']}/versions/{tool['version']}").json()["name"]
    body = _scenario_body(tool, fixture_hash, expected_value=99)
    body["id"] = first["id"]
    body["expected"]["checks"][1]["tool"] = name
    response = _post(client, "/scenarios", body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_exists"
    assert response.json()["error"]["same_content"] is False
    assert _count(migrated_database, m.Scenario, m.Scenario.id == uuid.UUID(first["id"])) == 1


def test_duplicate_content_hash_is_rejected(client: TestClient) -> None:
    fixture_hash, tool = _catalog(client)
    first = _publish_scenario(client, tool, fixture_hash)
    oracle = client.get(f"/scenarios/{first['id']}/versions/1.0.0/oracle").json()
    public = client.get(f"/scenarios/{first['id']}/versions/1.0.0").json()
    clone = {
        "id": str(uuid.uuid4()),
        "version": "1.0.1",
        "slug": f"clone-{uuid.uuid4().hex[:8]}",
        "primary_category": public["primary_category"],
        "difficulty": "easy",
        "split": oracle["split"],
        "family_id": oracle["family_id"],
        "task": public["task"],
        "tools": public["tools"],
        "environment": {
            **public["environment"],
            "fault_schedule": oracle["environment"]["fault_schedule"],
        },
        "limits": public["limits"],
        "expected": oracle["expected"],
        "evaluation": oracle["evaluation"],
    }
    # El hash ignora id/slug/version distintos si el resto coincide? No: id está en el
    # documento pero se excluye; slug y version sí entran. Igualamos slug+version.
    clone["slug"] = public["slug"]
    clone["version"] = "1.0.0"
    response = _post(client, "/scenarios", clone)
    assert response.status_code == 409
    assert response.json()["error"]["code"] in {"content_duplicate", "version_exists"}


def test_dataset_with_broken_ref_is_not_created(
    client: TestClient, migrated_database: Engine
) -> None:
    fixture_hash, tool = _catalog(client)
    scenario = _publish_scenario(client, tool, fixture_hash)
    name = f"broken-{uuid.uuid4().hex[:8]}"
    response = _post(
        client,
        "/datasets",
        {
            "version": "1.0.0",
            "name": name,
            "license": "MIT",
            "generator_version": "1.0.0",
            "scenario_refs": [
                {
                    "id": scenario["id"],
                    "version": scenario["version"],
                    "content_hash": scenario["content_hash"],
                },
                {
                    "id": str(uuid.uuid4()),
                    "version": "1.0.0",
                    "content_hash": scenario["content_hash"],
                },
            ],
            "fixture_refs": [fixture_hash],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"
    assert _count(migrated_database, m.Dataset, m.Dataset.name == name) == 0


def test_dataset_rejects_family_across_splits(
    client: TestClient, migrated_database: Engine
) -> None:
    fixture_hash, tool = _catalog(client)
    family = "shared-family"
    first = _publish_scenario(client, tool, fixture_hash, split="dev", family=family)
    second = _publish_scenario(client, tool, fixture_hash, split="held-out", family=family)
    name = f"cross-{uuid.uuid4().hex[:8]}"
    response = _post(
        client,
        "/datasets",
        {
            "version": "1.0.0",
            "name": name,
            "license": "MIT",
            "generator_version": "1.0.0",
            "scenario_refs": [
                {
                    "id": first["id"],
                    "version": first["version"],
                    "content_hash": first["content_hash"],
                },
                {
                    "id": second["id"],
                    "version": second["version"],
                    "content_hash": second["content_hash"],
                },
            ],
            "fixture_refs": [fixture_hash],
        },
    )
    assert response.status_code == 422
    assert "cruzar" in response.json()["error"]["message"]
    assert _count(migrated_database, m.Dataset, m.Dataset.name == name) == 0


def test_small_dataset_is_incomplete_not_complete_v1(client: TestClient) -> None:
    fixture_hash, tool = _catalog(client)
    scenario = _publish_scenario(client, tool, fixture_hash)
    response = _post(
        client,
        "/datasets",
        {
            "version": "1.0.0",
            "name": f"set-{uuid.uuid4().hex[:8]}",
            "license": "MIT",
            "generator_version": "1.0.0",
            "scenario_refs": [
                {
                    "id": scenario["id"],
                    "version": scenario["version"],
                    "content_hash": scenario["content_hash"],
                }
            ],
            "fixture_refs": [fixture_hash],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["coverage_class"] == "incomplete"
    assert response.json()["coverage_class"] != "complete_v1"
    public = client.get(
        f"/datasets/{response.json()['id']}/versions/{response.json()['version']}"
    ).json()
    assert public["scenario_refs"][0]["content_hash"] == scenario["content_hash"]
    assert "expected" not in str(public)


def test_pilot_dataset_is_labelled_pilot(client: TestClient) -> None:
    fixture_hash, tool = _catalog(client)
    refs = []
    for category in PRIMARY_CATEGORIES:
        for index in range(2):
            published = _publish_scenario(
                client, tool, fixture_hash, category=category, family=f"{category}-{index}"
            )
            refs.append(
                {
                    "id": published["id"],
                    "version": published["version"],
                    "content_hash": published["content_hash"],
                }
            )
    response = _post(
        client,
        "/datasets",
        {
            "version": "1.0.0",
            "name": f"pilot-{uuid.uuid4().hex[:8]}",
            "license": "MIT",
            "generator_version": "1.0.0",
            "scenario_refs": refs,
            "fixture_refs": [fixture_hash],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["coverage_class"] == "pilot"
    assert response.json()["category_counts"]["reasoning"] == 2


def test_benchmark_requires_matching_dataset_digest(
    client: TestClient, migrated_database: Engine
) -> None:
    fixture_hash, tool = _catalog(client)
    scenario = _publish_scenario(client, tool, fixture_hash)
    dataset = _post(
        client,
        "/datasets",
        {
            "version": "1.0.0",
            "name": f"set-{uuid.uuid4().hex[:8]}",
            "license": "MIT",
            "generator_version": "1.0.0",
            "scenario_refs": [
                {
                    "id": scenario["id"],
                    "version": scenario["version"],
                    "content_hash": scenario["content_hash"],
                }
            ],
            "fixture_refs": [fixture_hash],
        },
    ).json()
    ghost = str(uuid.uuid4())
    response = _post(
        client,
        "/benchmarks",
        {
            "id": ghost,
            "version": "1.0.0",
            "dataset_ref": {
                "id": dataset["id"],
                "version": dataset["version"],
                "content_hash": canonical_digest({"no": True}),
            },
            "evaluator_suite": {"name": "deterministic-core"},
            "metric_profile": {"primary": "task_success"},
            "comparison_rules": {},
        },
    )
    assert response.status_code == 422
    assert _count(migrated_database, m.Benchmark, m.Benchmark.id == uuid.UUID(ghost)) == 0
    created = _post(
        client,
        "/benchmarks",
        {
            "version": "1.0.0",
            "dataset_ref": {
                "id": dataset["id"],
                "version": dataset["version"],
                "content_hash": dataset["content_hash"],
            },
            "evaluator_suite": {"name": "deterministic-core"},
            "metric_profile": {"primary": "task_success"},
            "comparison_rules": {},
            "default_repetitions": 5,
            "seed_schedule": [11, 23, 37, 53, 71],
        },
    )
    assert created.status_code == 201, created.text
    read = client.get(f"/benchmarks/{created.json()['id']}/versions/1.0.0")
    assert read.json()["dataset_ref"]["content_hash"] == dataset["content_hash"]


def test_published_scenario_is_immutable(client: TestClient, migrated_database: Engine) -> None:
    fixture_hash, tool = _catalog(client)
    published = _publish_scenario(client, tool, fixture_hash)
    with (
        Session(migrated_database) as db,
        pytest.raises(DBAPIError, match="published rows are immutable on scenarios"),
    ):
        db.execute(
            update(m.Scenario)
            .where(m.Scenario.id == uuid.UUID(published["id"]))
            .values(oracle_ref={"checks": []})
        )


def test_client_hash_must_match_canonical_document(client: TestClient) -> None:
    fixture_hash, tool = _catalog(client)
    name = client.get(f"/tool-definitions/{tool['id']}/versions/{tool['version']}").json()["name"]
    body = _scenario_body(tool, fixture_hash)
    body["expected"]["checks"][1]["tool"] = name
    parsed = ScenarioCreate.model_validate(body)
    document = scenario_document(parsed, parsed.id or uuid.uuid4())
    computed = canonical_digest({k: v for k, v in document.items() if k not in HASH_EXCLUDE})
    body["content_hash"] = canonical_digest({"wrong": True})
    response = _post(client, "/scenarios", body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "hash_mismatch"
    body["content_hash"] = computed
    # El id forma parte del documento enviado al hasher pero se excluye: sin id el hash
    # coincide con el que calculará el servidor al asignar uno.
    ok = _post(client, "/scenarios", body)
    assert ok.status_code == 201, ok.text
