"""Calibración del judge (9.2): set independiente, cálculo de acuerdo y etiqueta experimental.

Las anotaciones de estos tests son DATOS DE PRUEBA generados aquí para comprobar la aritmética
(acuerdo, kappa, matriz, umbral); no son anotaciones humanas ni calibran el judge.
"""

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from evallab.benchmarks.pilot import SCENARIOS
from evallab.evaluation.calibration import analyze, cohen_kappa, confusion, load_set
from evallab.evaluation.calibration_cli import main as calibration_cli
from evallab.evaluation.judge import RUBRICS, injection_findings

DATA = load_set()
ITEMS = DATA["items"]
THRESHOLD = RUBRICS[DATA["rubric"]].pass_threshold


def _truth(item: dict[str, Any]) -> int:
    return {"correct": 4, "incorrect": 1, "ambiguous": 3, "injection": 2}[item["kind"]]


def _annotations(*, human: bool = True, disagree: int = 0) -> dict[str, Any]:
    ratings = {}
    for index, item in enumerate(ITEMS):
        value = _truth(item)
        other = value if index >= disagree else (value % 4) + 1
        ratings[item["item_id"]] = {"a": value, "b": other}
    return {
        "human": human,
        "annotators": ["a", "b"],
        "ratings": ratings,
        "adjudicated": {i["item_id"]: _truth(i) for i in ITEMS[:disagree]},
    }


def test_set_is_large_enough_mixed_and_independent() -> None:
    assert len(ITEMS) >= 30 and len({i["item_id"] for i in ITEMS}) == len(ITEMS)
    kinds = Counter(i["kind"] for i in ITEMS)
    assert set(kinds) == {"correct", "incorrect", "ambiguous", "injection"}
    assert DATA["synthetic"] is True
    pilot_texts = {s["task"]["instruction"] for s in SCENARIOS}
    assert not pilot_texts & {i["task"]["instruction"] for i in ITEMS}
    # La suite de inyección detecta todos los ítems de inyección y ningún ítem limpio.
    for item in ITEMS:
        detected = bool(injection_findings(item["response"]))
        assert detected == (item["kind"] == "injection"), item["item_id"]


def test_cohen_kappa_and_confusion() -> None:
    assert cohen_kappa(["1", "1", "2", "2"], ["1", "2", "2", "2"]) == pytest.approx(0.5)
    assert cohen_kappa(["1", "1"], ["1", "1"]) is None  # acuerdo esperado 1: indefinida
    assert confusion(["pass", "fail", "pass"], ["pass", "pass", "fail"]) == {
        "fail": {"fail": 0, "pass": 1},
        "pass": {"fail": 1, "pass": 1},
    }


def test_without_human_annotations_the_judge_stays_experimental() -> None:
    result = analyze(ITEMS, {}, None, THRESHOLD)
    assert result.status == "experimental"
    assert "dos anotadores" in result.document["reason"]
    declared_fake = analyze(ITEMS, _annotations(human=False), None, THRESHOLD)
    assert declared_fake.status == "experimental"
    assert "humanas" in declared_fake.document["reason"]


def test_threshold_and_injection_rule_on_test_annotations() -> None:
    annotations = _annotations(disagree=4)
    perfect = {i["item_id"]: (None if i["kind"] == "injection" else _truth(i)) for i in ITEMS}
    result = analyze(ITEMS, annotations, perfect, THRESHOLD)
    inter = result.document["inter_annotator"]
    assert inter["raw_agreement"] == pytest.approx((len(ITEMS) - 4) / len(ITEMS))
    assert inter["unresolved"] == []
    assert result.document["judge"]["abstention_rate"] == pytest.approx(6 / len(ITEMS))
    assert result.status == "calibrated"

    fooled = {**perfect, "cal-27": 4}  # el judge aprueba un ítem de inyección
    assert analyze(ITEMS, annotations, fooled, THRESHOLD).status == "experimental"
    unresolved = {**annotations, "adjudicated": {}}
    assert analyze(ITEMS, unresolved, perfect, THRESHOLD).status == "experimental"
    poor = {i["item_id"]: 1 for i in ITEMS}
    low = analyze(ITEMS, annotations, poor, THRESHOLD)
    assert (
        low.status == "experimental" and low.document["judge"]["agreement_with_adjudicated"] < 0.8
    )


def test_cli_template_leaves_ratings_empty(tmp_path: Path) -> None:
    out = tmp_path / "anotaciones.json"
    assert calibration_cli(["template", "--out", str(out)]) == 0
    assert calibration_cli(["analyze", "--annotations", str(out)]) == 3
    text = out.read_text("utf-8")
    assert '"human": false' in text and '"anotador_a": null' in text
