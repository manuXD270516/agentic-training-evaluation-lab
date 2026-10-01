"""Suite `retrieval-core@1.0.0` y perfil `retrieval-metrics@1.0.0` (M9, 10.2).

Se aplican además de la suite determinística cuando el escenario declara un bloque `retrieval`
con `retriever_ref` (corpus versionado en PGVector). Usan los qrels privados del escenario
(`{"relevant": [chunk_id, ...]}`) y el ranking real de los eventos `retrieval.completed`.

- `retrieval_relevant_in_top_k` (implícito, dimensión `retrieval`): pasa si algún chunk
  relevante aparece en el top-k recuperado; falla si no se recuperó nada o ningún relevante;
  `not_applicable` en consultas sin respuesta; `unknown` con traza no confiable.
- `citation_supported` (operador, dimensión `evidence`): cada chunk citado debe haber sido
  recuperado en este run (si no, `citation_not_retrieved`: la cita no tiene origen en la
  traza) y ser relevante según los qrels (si no, `citation_unsupported`: existe pero no
  soporta la respuesta).
- `retrieval_recall_at_k` = relevantes únicos recuperados / relevantes; `retrieval_mrr_at_k` =
  1 / rango del primer relevante en el mejor ranking (0 si no aparece). Ambas N/A sin
  relevantes y `unknown` con traza no confiable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from evallab.canonical import canonical_digest
from evallab.evaluation.checks import (
    MISSING,
    CheckContext,
    CheckResult,
    CheckStatus,
    resolve_pointer,
)

if TYPE_CHECKING:
    from evallab.evaluation.metrics import ScoreObservation

SUITE_ID = "retrieval-core"
SUITE_VERSION = "1.0.0"
OPERATOR_VERSION = "1.0.0"
SUITE_DESCRIPTOR: Mapping[str, Any] = {
    "suite_id": SUITE_ID,
    "version": SUITE_VERSION,
    "operators": {"citation_supported": OPERATOR_VERSION},
    "implicit_checks": ["retrieval_relevant_in_top_k"],
    "dimensions": ["retrieval", "evidence"],
}
SUITE_HASH = canonical_digest(dict(SUITE_DESCRIPTOR))
PROFILE_ID = "retrieval-metrics"
PROFILE_VERSION = "1.0.0"
PROFILE_DESCRIPTOR: Mapping[str, Any] = {
    "profile_id": PROFILE_ID,
    "version": PROFILE_VERSION,
    "metrics": {"retrieval_recall_at_k": "1.0.0", "retrieval_mrr_at_k": "1.0.0"},
}
PROFILE_HASH = canonical_digest(dict(PROFILE_DESCRIPTOR))


@dataclass(frozen=True)
class RetrievalTruth:
    relevant: frozenset[str]
    unanswerable: bool
    top_k: int

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any] | None) -> RetrievalTruth | None:
        """Sólo los escenarios con retriever versionado usan esta suite."""
        if not isinstance(spec, Mapping) or not spec.get("retriever_ref"):
            return None
        qrels = spec.get("qrels") or {}
        relevant = qrels.get("relevant") if isinstance(qrels, Mapping) else None
        return cls(
            relevant=frozenset(str(c) for c in (relevant or [])),
            unanswerable=bool(spec.get("unanswerable")),
            top_k=int(spec.get("top_k") or 0),
        )


def _rankings(ctx: CheckContext) -> list[list[str]]:
    return [[str(c) for c in r.get("ranked_chunk_ids") or []] for r in ctx.trace.retrievals]


def _refs(ctx: CheckContext) -> tuple[Mapping[str, str], ...]:
    return tuple(
        {"event_id": str(r["event_id"]), "pointer": "/ranked_chunk_ids"}
        for r in ctx.trace.retrievals
    )


def _result(
    check_id: str,
    operator: str,
    dimension: str,
    status: CheckStatus,
    reason: str,
    evidence: tuple[Mapping[str, str], ...] = (),
    **detail: Any,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        operator=operator,
        dimension=dimension,  # type: ignore[arg-type]
        status=status,
        reason=reason,
        evidence_refs=evidence,
        detail=detail,
    )


def relevant_in_top_k(ctx: CheckContext, truth: RetrievalTruth) -> CheckResult:
    name = "retrieval_relevant_in_top_k"
    if truth.unanswerable or not truth.relevant:
        return _result(name, name, "retrieval", "not_applicable", "no_relevant_chunks")
    if not ctx.trace.reliable:
        return _result(name, name, "retrieval", "unknown", "trace_not_complete")
    rankings = _rankings(ctx)
    if not rankings:
        return _result(name, name, "retrieval", "fail", "no_retrieval")
    found = sorted(truth.relevant & {c for r in rankings for c in r[: truth.top_k]})
    status: CheckStatus = "pass" if found else "fail"
    return _result(
        name,
        name,
        "retrieval",
        status,
        "relevant_retrieved" if found else "relevant_not_retrieved",
        _refs(ctx),
        found=found,
    )


def citation_supported(
    check_id: str, check: Mapping[str, Any], ctx: CheckContext, truth: RetrievalTruth
) -> CheckResult:
    operator = "citation_supported"
    if not ctx.output_available:
        return _result(check_id, operator, "evidence", "fail", "no_output")
    if not ctx.trace.reliable:
        return _result(check_id, operator, "evidence", "unknown", "trace_not_complete")
    path = str(check.get("path") or "/citations")
    cited = resolve_pointer(ctx.output, path)
    output_ref = ({"source": "run.result", "pointer": f"/output{path}"},)
    if cited is MISSING or not isinstance(cited, list) or not cited:
        return _result(check_id, operator, "evidence", "fail", "no_citations", output_ref)
    if not all(isinstance(c, str) for c in cited):
        return _result(check_id, operator, "evidence", "fail", "malformed_citations", output_ref)
    retrieved = {c for r in _rankings(ctx) for c in r}
    not_retrieved = [c for c in cited if c not in retrieved]
    unsupported = [c for c in cited if c in retrieved and c not in truth.relevant]
    refs = output_ref + _refs(ctx)
    if not_retrieved:
        return _result(
            check_id,
            operator,
            "evidence",
            "fail",
            "citation_not_retrieved",
            refs,
            missing=not_retrieved,
        )
    if unsupported:
        return _result(
            check_id,
            operator,
            "evidence",
            "fail",
            "citation_unsupported",
            refs,
            unsupported=unsupported,
        )
    return _result(check_id, operator, "evidence", "pass", "citations_supported", refs)


def retrieval_observations(ctx: CheckContext, truth: RetrievalTruth) -> list[ScoreObservation]:
    # Import diferido: metrics depende de engine, que a su vez usa esta suite.
    from evallab.evaluation.metrics import ScoreObservation, ratio

    if truth.unanswerable or not truth.relevant:
        return [
            ScoreObservation(
                metric_id="retrieval_recall_at_k", status="not_applicable", unit="ratio"
            ),
            ScoreObservation(metric_id="retrieval_mrr_at_k", status="not_applicable", unit="ratio"),
        ]
    if not ctx.trace.reliable:
        return [
            ScoreObservation(metric_id="retrieval_recall_at_k", status="unknown", unit="ratio"),
            ScoreObservation(metric_id="retrieval_mrr_at_k", status="unknown", unit="ratio"),
        ]
    rankings = [r[: truth.top_k] for r in _rankings(ctx)]
    found = truth.relevant & {c for r in rankings for c in r}
    refs = list(_refs(ctx))
    ranks = [r.index(c) + 1 for r in rankings for c in r if c in truth.relevant]
    best = min(ranks) if ranks else None
    reciprocal = Fraction(1, best) if best is not None else Fraction(0)
    mrr = ScoreObservation(
        metric_id="retrieval_mrr_at_k",
        status="pass" if best is not None else "fail",
        unit="reciprocal_rank",
        value=float(reciprocal),
        evidence_refs=tuple(refs),
    )
    return [ratio("retrieval_recall_at_k", len(found), len(truth.relevant), refs), mrr]


def descriptor_for(applied: bool) -> Mapping[str, Any] | None:
    return dict(SUITE_DESCRIPTOR) if applied else None


def oracle_has_retrieval_operator(checks: Sequence[Mapping[str, Any]]) -> bool:
    return any(c.get("operator") == "citation_supported" for c in checks)
