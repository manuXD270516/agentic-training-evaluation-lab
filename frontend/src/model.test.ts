import { describe, expect, it } from "vitest";
import { pageTrace, traceQuery } from "./api/client";
import type { ScoreStatus } from "./api/types";
import {
  ALL,
  EMPTY_FILTER,
  categoriesOf,
  countStatuses,
  filterCells,
  formatRange,
  href,
  parseRoute,
  statusDisplay,
} from "./model";
import { EVENT, RUN, cell, trace } from "./testing/fixtures";
import { cursorBefore } from "./views/RunView";

describe("statusDisplay", () => {
  it("gives N/A, unknown, error and unevaluated distinct labels", () => {
    const keys: (ScoreStatus | null)[] = ["not_applicable", "unknown", "error", null];
    const labels = keys.map((k) => statusDisplay(k).label);
    expect(labels).toEqual(["N/A", "unknown", "error", "sin evaluar"]);
    expect(new Set(keys.map((k) => statusDisplay(k).title)).size).toBe(4);
    expect(statusDisplay("not_applicable").title).toContain("no entra en N");
    expect(statusDisplay("error").title).toContain("unknown");
  });
});

describe("filterCells", () => {
  const cells = [
    cell("reasoning", "a", "pass"),
    cell("reasoning", "b", "not_applicable"),
    cell("retrieval", "a", "error"),
    cell("retrieval", "b", null),
    cell("tool_use", "a", "unknown"),
  ];

  it("filters by category", () => {
    const rows = filterCells(cells, { ...EMPTY_FILTER, category: "retrieval" });
    expect(rows.map((c) => c.task_success)).toEqual(["error", null]);
  });

  it("filters by agent and by status without merging N/A, unknown and error", () => {
    expect(filterCells(cells, { ...EMPTY_FILTER, agent: "b" })).toHaveLength(2);
    for (const status of ["not_applicable", "unknown", "error", "unevaluated"] as const) {
      expect(filterCells(cells, { ...EMPTY_FILTER, status })).toHaveLength(1);
    }
  });

  it("counts every status, zeros included", () => {
    expect(countStatuses(cells)).toEqual({
      pass: 1,
      fail: 0,
      unknown: 1,
      not_applicable: 1,
      error: 1,
      unevaluated: 1,
    });
    expect(categoriesOf(cells)).toEqual(["reasoning", "retrieval", "tool_use"]);
    expect(filterCells(cells, { category: ALL, agent: ALL, status: ALL })).toHaveLength(5);
  });
});

describe("routes", () => {
  it("parses experiment mode and evidence event routes", () => {
    expect(parseRoute("")).toEqual({ view: "experiments" });
    expect(parseRoute(`#/experiments/${RUN}?mode=replay`)).toEqual({
      view: "experiment",
      id: RUN,
      mode: "replay",
    });
    expect(parseRoute(`#/experiments/${RUN}?mode=bogus`)).toMatchObject({ mode: "live" });
    expect(parseRoute(href.event(RUN, EVENT))).toEqual({ view: "run", id: RUN, eventId: EVENT });
    expect(parseRoute(href.run(RUN))).toEqual({ view: "run", id: RUN, eventId: null });
    expect(parseRoute("#/runs/not-a-uuid").view).toBe("not_found");
  });

  it("round-trips experiment links", () => {
    expect(parseRoute(href.experiment(RUN, "replay"))).toMatchObject({ mode: "replay" });
    expect(href.experiment(RUN)).toBe(`#/experiments/${RUN}`);
  });
});

describe("trace paging", () => {
  it("builds keyset queries and opens just before the cited event", () => {
    expect(traceQuery(null, 50)).toBe("limit=50");
    expect(traceQuery(40, 50)).toBe("limit=50&after_sequence=40");
    expect(cursorBefore(1)).toBeNull();
    expect(cursorBefore(3)).toBeNull();
    expect(cursorBefore(10)).toBe(7);
  });

  it("pages a full static trace like the API keyset pagination", () => {
    const base = trace("complete");
    const event = base.events[0]!;
    const full = {
      ...base,
      page: null,
      events: [1, 2, 3].map((sequence) => ({ ...event, event_id: `e${sequence}`, sequence })),
    };
    const first = pageTrace(full, null, 2);
    expect(first.events.map((e) => e.sequence)).toEqual([1, 2]);
    expect(first.page?.next_after_sequence).toBe(2);
    const last = pageTrace(full, 2, 2);
    expect(last.events.map((e) => e.sequence)).toEqual([3]);
    expect(last.page?.next_after_sequence).toBeNull();
    expect(pageTrace(full, null, 3).page?.next_after_sequence).toBeNull();
  });

  it("formats missingness ranges", () => {
    expect(formatRange([0.7, 0.8])).toBe("[0.700, 0.800]");
    expect(formatRange(null)).toBe("—");
  });
});
