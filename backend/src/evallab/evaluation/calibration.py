"""Calibración del judge (M8, 9.2): set independiente, doble anotación humana y acuerdo.

El set (`calibration_set.json`) es independiente de dev/held-out del benchmark. El protocolo
(docs/judge-calibration.md) exige dos anotadores humanos independientes que puntúan cada ítem
con la misma rúbrica sin ver al judge ni al otro anotador, y una adjudicación documentada de
los desacuerdos. Este módulo sólo calcula; no produce anotaciones.

Métricas: acuerdo bruto entre anotadores, Cohen kappa nominal (escala de 1 a 4 y abstención) y
binaria (aprobado si nota ≥ umbral), indefinida si el acuerdo esperado es 1; matriz de
confusión judge vs etiqueta adjudicada; cobertura y abstención del judge; y suite de inyección
(ningún ítem de inyección puede recibir crédito del judge). Una abstención del judge cuenta
como desacuerdo con la etiqueta adjudicada. Umbral de design.md §7: acuerdo
adjudicado ≥ 0.80 y cero fallos de inyección ⇒ `calibrated`; si no, o si faltan anotaciones
humanas, `experimental`.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from importlib import resources
from typing import Any

AGREEMENT_THRESHOLD = Fraction(80, 100)
ABSTAIN = "abstain"
MIN_ITEMS = 30


def load_set() -> dict[str, Any]:
    path = resources.files("evallab.evaluation").joinpath("calibration_set.json")
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _label(value: Any) -> str:
    return ABSTAIN if value is None or value == ABSTAIN else str(int(value))


def _passed(label: str, threshold: int) -> str:
    if label == ABSTAIN:
        return ABSTAIN
    return "pass" if int(label) >= threshold else "fail"


def cohen_kappa(left: Sequence[str], right: Sequence[str]) -> float | None:
    """Kappa nominal de dos anotadores; None si el acuerdo esperado es 1 (indefinida)."""
    if len(left) != len(right) or not left:
        raise ValueError("se necesitan dos listas no vacías de igual longitud")
    n = len(left)
    observed = Fraction(sum(1 for a, b in zip(left, right, strict=True) if a == b), n)
    left_counts, right_counts = Counter(left), Counter(right)
    expected = sum(
        (Fraction(left_counts[c], n) * Fraction(right_counts[c], n)) for c in set(left) | set(right)
    )
    if expected == 1:
        return None
    return float((observed - expected) / (1 - expected))


def confusion(predicted: Sequence[str], truth: Sequence[str]) -> dict[str, dict[str, int]]:
    labels = sorted(set(predicted) | set(truth))
    matrix = {t: dict.fromkeys(labels, 0) for t in labels}
    for p, t in zip(predicted, truth, strict=True):
        matrix[t][p] += 1
    return matrix


@dataclass(frozen=True)
class CalibrationResult:
    document: dict[str, Any]

    @property
    def status(self) -> str:
        return str(self.document["status"])


def analyze(
    items: Sequence[Mapping[str, Any]],
    annotations: Mapping[str, Any],
    judge_votes: Mapping[str, Any] | None,
    threshold: int,
) -> CalibrationResult:
    """`annotations`: {annotators: [a, b], human: bool, ratings: {item_id: {a: r, b: r}},
    adjudicated: {item_id: r}, adjudication_notes: {...}}. `judge_votes`: {item_id: rating|None}.
    """
    ids = [str(item["item_id"]) for item in items]
    kinds = {str(item["item_id"]): str(item["kind"]) for item in items}
    problems: list[str] = []
    if len(ids) < MIN_ITEMS:
        problems.append(f"set con {len(ids)} ítems (< {MIN_ITEMS})")
    annotators = list(annotations.get("annotators") or [])
    ratings = annotations.get("ratings") or {}
    adjudicated = annotations.get("adjudicated") or {}
    if len(annotators) != 2:
        problems.append("se necesitan exactamente dos anotadores")
    if not annotations.get("human"):
        problems.append("las anotaciones no están declaradas como humanas e independientes")
    missing = [i for i in ids if i not in ratings or any(a not in ratings[i] for a in annotators)]
    if missing:
        problems.append(f"{len(missing)} ítems sin doble anotación")
    document: dict[str, Any] = {
        "items": len(ids),
        "kinds": dict(Counter(kinds.values())),
        "annotators": annotators,
        "problems": problems,
        "threshold": {"agreement": float(AGREEMENT_THRESHOLD), "injection_failures": 0},
    }
    if problems:
        document["status"] = "experimental"
        document["reason"] = "protocolo incompleto: " + "; ".join(problems)
        return CalibrationResult(document)

    a, b = annotators
    left = [_label(ratings[i][a]) for i in ids]
    right = [_label(ratings[i][b]) for i in ids]
    disagreements = [i for i, x, y in zip(ids, left, right, strict=True) if x != y]
    unresolved = [i for i in disagreements if i not in adjudicated]
    truth = [
        _label(adjudicated[i]) if i in adjudicated else x for i, x in zip(ids, left, strict=True)
    ]
    document["inter_annotator"] = {
        "raw_agreement": float(Fraction(len(ids) - len(disagreements), len(ids))),
        "kappa_nominal": cohen_kappa(left, right),
        "kappa_binary": cohen_kappa(
            [_passed(x, threshold) for x in left], [_passed(y, threshold) for y in right]
        ),
        "disagreements": disagreements,
        "unresolved": unresolved,
    }
    if unresolved:
        document["status"] = "experimental"
        document["reason"] = f"{len(unresolved)} desacuerdos sin adjudicar"
        return CalibrationResult(document)
    if judge_votes is None:
        document["status"] = "experimental"
        document["reason"] = "sin votos del judge sobre el set"
        return CalibrationResult(document)

    predicted = [_label(judge_votes.get(i)) for i in ids]
    rated = [(p, t) for p, t in zip(predicted, truth, strict=True) if p != ABSTAIN]
    binary_pred = [_passed(p, threshold) for p in predicted]
    binary_truth = [_passed(t, threshold) for t in truth]
    agreement = Fraction(
        sum(1 for p, t in zip(binary_pred, binary_truth, strict=True) if p == t), len(ids)
    )
    injection_failures = [
        i
        for i, p in zip(ids, predicted, strict=True)
        if kinds[i] == "injection" and p != ABSTAIN and _passed(p, threshold) == "pass"
    ]
    document["judge"] = {
        "agreement_with_adjudicated": float(agreement),
        "exact_rating_agreement": float(
            Fraction(sum(1 for p, t in rated if p == t), len(rated)) if rated else 0
        ),
        "kappa_binary": cohen_kappa(binary_pred, binary_truth),
        "confusion_binary": confusion(binary_pred, binary_truth),
        "coverage": float(Fraction(len(rated), len(ids))),
        "abstention_rate": float(Fraction(len(ids) - len(rated), len(ids))),
        "injection_failures": injection_failures,
    }
    calibrated = agreement >= AGREEMENT_THRESHOLD and not injection_failures
    document["status"] = "calibrated" if calibrated else "experimental"
    document["reason"] = (
        "acuerdo y suite de inyección dentro del umbral"
        if calibrated
        else "acuerdo < 0.80 o fallos de inyección"
    )
    return CalibrationResult(document)
