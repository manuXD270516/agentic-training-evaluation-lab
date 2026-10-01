import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CellsResponse } from "../api/types";
import { EMPTY_FILTER } from "../model";
import { EVENT, EXPERIMENT, RUN, cell, evaluation, report, trace } from "../testing/fixtures";
import { ExperimentDashboard, MetricsTable } from "./ExperimentView";
import { ExperimentsTable } from "./ExperimentsView";
import { EvaluationPanel, TraceEvents, TraceSummary } from "./RunView";

const cells: CellsResponse = {
  experiment_id: EXPERIMENT,
  manifest_hash: "b".repeat(64),
  mode: "live",
  planned_cells: 4,
  cells: [
    cell("reasoning", "agent-a", "pass"),
    cell("reasoning", "agent-a", null),
    cell("retrieval", "agent-a", "error", { trace_completeness: "incomplete" }),
    cell("retrieval", "agent-a", "not_applicable"),
  ],
};

function dashboard(category = EMPTY_FILTER.category): string {
  return renderToStaticMarkup(
    <ExperimentDashboard
      report={report}
      cells={cells}
      filter={{ ...EMPTY_FILTER, category }}
      onFilter={() => undefined}
    />,
  );
}

describe("ExperimentDashboard", () => {
  it("shows honesty labels and every planned cell, missing ones included", () => {
    const html = dashboard();
    expect(html).toContain("descriptive_only");
    expect(html).toContain("harness_baseline");
    expect(html).toContain("Celdas (4 de 4 programadas)");
    expect(html).toContain('class="muted">sin run<');
    expect(html).toContain('class="warn">incomplete');
  });

  it("filters the task_success summary and the cells by category", () => {
    const all = dashboard();
    const retrieval = dashboard("retrieval");
    expect(all).toContain("todas las categorías");
    expect(retrieval).toContain("Celdas (2 de 4 programadas)");
    // Todas: 7 éxitos y 1 unknown en 10 -> [0.700, 0.800]; retrieval: 3/5 sin unknown.
    expect(all).toContain("[0.700, 0.800]");
    expect(retrieval).toContain("[0.600, 0.600]");
    expect(retrieval).not.toContain('class="muted">sin run<');
  });

  it("links the other run mode instead of mixing live and replay", () => {
    expect(dashboard()).toContain(`href="#/experiments/${EXPERIMENT}?mode=replay"`);
  });

  it("renders N/A, unknown, error and unevaluated as distinct badges", () => {
    const html = dashboard();
    for (const key of ["pass", "not_applicable", "error", "unevaluated"]) {
      expect(html).toContain(`badge badge-${key}`);
    }
    expect(html).toContain(">N/A<");
    expect(html).toContain(">sin evaluar<");
  });
});

describe("MetricsTable", () => {
  it("keeps a column per status and the denominators", () => {
    const html = renderToStaticMarkup(<MetricsTable agent={report.agents[0]!} />);
    expect(html).toContain('class="count-not_applicable">3<');
    expect(html).toContain('class="count-unknown">2<');
    expect(html).toContain('class="count-error">1<');
    expect(html).toContain("(8/10)");
  });
});

describe("ExperimentsTable", () => {
  it("links only sealed experiments", () => {
    const base = {
      status: "draft",
      hypothesis: "borrador",
      benchmark: null,
      agents: [],
      repetitions: 1,
      seeds: [11],
      created_at: "2026-10-01T00:00:00Z",
      sealed_at: null,
    };
    const html = renderToStaticMarkup(
      <ExperimentsTable
        experiments={[
          { ...base, id: EXPERIMENT, manifest_hash: null },
          { ...base, id: RUN, hypothesis: "sellado", manifest_hash: "b".repeat(64) },
        ]}
      />,
    );
    expect(html).toContain(`href="#/experiments/${RUN}"`);
    expect(html).not.toContain(`href="#/experiments/${EXPERIMENT}"`);
  });
});

describe("Run view", () => {
  it("links score evidence to its trace event and shows versions", () => {
    const html = renderToStaticMarkup(<EvaluationPanel runId={RUN} evaluations={[evaluation]} />);
    expect(html).toContain(`href="#/runs/${RUN}/events/${EVENT}"`);
    expect(html).toContain("tool_accuracy@1.0.0");
    expect(html).toContain(">N/A<");
    expect(html).toContain("Perfil de métricas");
  });

  it("says when a run has no evaluation instead of showing scores", () => {
    const html = renderToStaticMarkup(<EvaluationPanel runId={RUN} evaluations={[]} />);
    expect(html).toContain("cuentan como unknown");
  });

  it("makes incomplete traces visible and highlights the cited event", () => {
    const incomplete = renderToStaticMarkup(<TraceSummary trace={trace("incomplete")} />);
    expect(incomplete).toContain('role="alert"');
    expect(incomplete).toContain("<strong>incomplete</strong>");
    const complete = renderToStaticMarkup(<TraceSummary trace={trace("complete")} />);
    expect(complete).not.toContain('role="alert"');
    const events = renderToStaticMarkup(
      <TraceEvents events={trace("complete").events} highlight={EVENT} />,
    );
    expect(events).toContain('class="event highlight"');
  });
});
