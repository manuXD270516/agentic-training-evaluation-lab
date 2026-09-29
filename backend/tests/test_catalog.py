from types import SimpleNamespace

import pytest

from evallab.canonical import canonical_digest
from evallab.domain.vocabulary import PRIMARY_CATEGORIES
from evallab.schemas import FixtureCreate
from evallab.services.catalog import HASH_EXCLUDE, coverage_class, fixture_document
from evallab.services.errors import InvalidRequestError


def _scenario(category: str, split: str, family: str) -> SimpleNamespace:
    return SimpleNamespace(primary_category=category, split=split, family_id=family)


def test_fixture_hash_excludes_identity_fields() -> None:
    data = FixtureCreate(name="clock", payload={"t": 0})
    document = fixture_document(data)
    assert set(document) == {"name", "payload"}
    assert canonical_digest(document) == canonical_digest({"name": "clock", "payload": {"t": 0}})
    assert {"id", "content_hash", "created_at", "digest"} <= HASH_EXCLUDE


def test_pilot_coverage_is_fourteen_dev_cases() -> None:
    rows: list[SimpleNamespace] = [
        _scenario(category, "dev", f"{category}-{i}")
        for category in PRIMARY_CATEGORIES
        for i in range(2)
    ]
    assert coverage_class(rows) == "pilot"


def test_complete_v1_requires_seventy_cases_and_split() -> None:
    rows: list[SimpleNamespace] = []
    for category in PRIMARY_CATEGORIES:
        rows.extend(_scenario(category, "dev", f"{category}-d-{i}") for i in range(6))
        rows.extend(_scenario(category, "held-out", f"{category}-h-{i}") for i in range(4))
    assert len(rows) == 70
    assert coverage_class(rows) == "complete_v1"


def test_other_counts_are_incomplete() -> None:
    assert coverage_class([_scenario("reasoning", "dev", "a")]) == "incomplete"


def test_family_cannot_cross_splits() -> None:
    rows = [
        _scenario("reasoning", "dev", "same-family"),
        _scenario("reasoning", "held-out", "same-family"),
    ]
    with pytest.raises(InvalidRequestError, match="cruzar splits"):
        coverage_class(rows)
