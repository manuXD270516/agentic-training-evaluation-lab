import { useEffect, useState } from "react";
import { api, rawLinks, TRACE_PAGE_SIZE } from "../api/client";
import type { Evaluation, Run, Trace, TraceEvent } from "../api/types";
import { Hash, Legend, Loadable, StatusBadge } from "../components";
import { useAsync } from "../hooks";
import { formatRate, href } from "../model";

/** Eventos de contexto que se muestran antes del evento citado como evidencia. */
export const EVIDENCE_CONTEXT = 2;

/** Cursor para abrir la traza en el evento citado (las secuencias empiezan en 1). */
export function cursorBefore(sequence: number, context = EVIDENCE_CONTEXT): number | null {
  const after = sequence - 1 - context;
  return after > 0 ? after : null;
}

export function RunHeader({ run }: { run: Run }) {
  const attribution = run.result?.["attribution"];
  return (
    <dl className="facts">
      <dt>Experimento</dt>
      <dd>
        <a href={href.experiment(run.experiment_id, run.mode)}>{run.experiment_id.slice(0, 8)}</a>
      </dd>
      <dt>Estado</dt>
      <dd>
        {run.status}
        {run.error_class ? ` · ${run.error_class}` : ""}
      </dd>
      <dt>Modo</dt>
      <dd>
        {run.mode}
        {run.source_run_id ? (
          <>
            {" "}
            · origen <a href={href.run(run.source_run_id)}>{run.source_run_id.slice(0, 8)}</a>
          </>
        ) : null}
      </dd>
      <dt>Escenario</dt>
      <dd>
        <a href={rawLinks.scenario(run.scenario.id, run.scenario.version)}>
          {run.scenario.id.slice(0, 8)}@{run.scenario.version}
        </a>{" "}
        <span className="muted">(vista pública, sin oráculo)</span>
      </dd>
      <dt>Agente</dt>
      <dd>
        <a href={rawLinks.agent(run.agent.id, run.agent.version)}>
          {run.agent.id.slice(0, 8)}@{run.agent.version}
        </a>
        {typeof attribution === "string" ? (
          <>
            {" "}
            · <code>{attribution}</code>
          </>
        ) : null}
      </dd>
      <dt>Repetición / seed</dt>
      <dd>
        {run.repetition} / {run.seed}
      </dd>
    </dl>
  );
}

export function EvaluationPanel({
  runId,
  evaluations,
}: {
  runId: string;
  evaluations: Evaluation[];
}) {
  const latest = evaluations.at(-1);
  if (!latest) {
    return <p className="muted">Run sin evaluaciones: todas sus métricas cuentan como unknown.</p>;
  }
  const history = evaluations.slice(0, -1);
  return (
    <>
      <dl className="facts">
        <dt>Evaluación</dt>
        <dd>
          {latest.id.slice(0, 8)} · {latest.status}
          {latest.error ? ` · ${latest.error}` : ""}
        </dd>
        <dt>Suite</dt>
        <dd>
          {latest.evaluator_suite_version ?? "—"} <Hash value={latest.evaluator_suite_hash} />
        </dd>
        <dt>Perfil de métricas</dt>
        <dd>
          {latest.metric_profile_version ?? "—"} <Hash value={latest.metric_profile_hash} />
        </dd>
        <dt>Traza evaluada</dt>
        <dd>
          <Hash value={latest.trace_digest} />
        </dd>
        {history.length > 0 ? (
          <>
            <dt>Evaluaciones anteriores</dt>
            <dd>{history.map((e) => `${e.id.slice(0, 8)} (${e.status})`).join(", ")}</dd>
          </>
        ) : null}
      </dl>
      <Legend />
      <table>
        <thead>
          <tr>
            <th>Métrica</th>
            <th>Ámbito</th>
            <th>Estado</th>
            <th>Valor</th>
            <th>Evidencia</th>
          </tr>
        </thead>
        <tbody>
          {latest.scores.map((score) => (
            <tr key={`${score.scope}:${score.metric_id}`}>
              <td>
                <code>
                  {score.metric_id}@{score.metric_version}
                </code>
              </td>
              <td>{score.scope}</td>
              <td>
                <StatusBadge status={score.status} />
              </td>
              <td>
                {formatRate(score.value)}
                {score.denominator !== null ? (
                  <span className="muted">
                    {" "}
                    ({score.numerator}/{score.denominator})
                  </span>
                ) : null}
              </td>
              <td>
                {score.evidence_refs.length === 0 ? (
                  <span className="muted">—</span>
                ) : (
                  score.evidence_refs.map((ref, index) =>
                    ref.event_id ? (
                      <a
                        key={`${ref.event_id}:${index}`}
                        className="evidence"
                        href={href.event(runId, ref.event_id)}
                        title={ref.pointer ? `pointer ${ref.pointer}` : "evento"}
                      >
                        {ref.event_id.slice(0, 8)}
                        {ref.pointer ? ` ${ref.pointer}` : ""}
                      </a>
                    ) : (
                      <code key={index} className="evidence" title="Evidencia fuera de la traza">
                        {typeof ref["source"] === "string" ? ref["source"] : "ref"}{" "}
                        {ref.pointer ?? ""}
                      </code>
                    ),
                  )
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function TraceSummary({ trace }: { trace: Trace }) {
  const incomplete = trace.completeness !== "complete";
  return (
    <>
      {incomplete ? (
        <p className="banner-warn" role="alert">
          Traza <strong>{trace.completeness ?? "sin sellar"}</strong>: faltan eventos o el run no
          terminó. Las métricas de proceso de este run son unknown, no pass ni fail.
        </p>
      ) : null}
      <p className="muted">
        {trace.event_count} eventos · esquema {trace.schema_version} · digest{" "}
        <Hash value={trace.digest} /> ·{" "}
        <a href={rawLinks.traceExport(trace.run_id)}>export JSONL</a>
      </p>
    </>
  );
}

export function TraceEvents({
  events,
  highlight,
}: {
  events: TraceEvent[];
  highlight: string | null;
}) {
  return (
    <ol className="events">
      {events.map((event) => (
        <li
          key={event.event_id}
          id={`event-${event.event_id}`}
          className={event.event_id === highlight ? "event highlight" : "event"}
          value={event.sequence}
        >
          <div>
            <strong>{event.type}</strong> <span className="muted">{event.actor_role}</span>{" "}
            <span className="muted">+{event.elapsed_ms} ms</span>{" "}
            <code className="muted">{event.event_id.slice(0, 8)}</code>
          </div>
          <pre>{JSON.stringify(event.payload, null, 2)}</pre>
        </li>
      ))}
    </ol>
  );
}

interface TraceState {
  trace: Trace | null;
  events: TraceEvent[];
  next: number | null;
  start: number | null;
  error: string | null;
  loading: boolean;
}

function TracePanel({ runId, eventId }: { runId: string; eventId: string | null }) {
  const [state, setState] = useState<TraceState>({
    trace: null,
    events: [],
    next: null,
    start: null,
    error: null,
    loading: true,
  });

  const load = (after: number | null, append: boolean) => {
    setState((s) => ({ ...s, loading: true, error: null }));
    api.tracePage(runId, after, TRACE_PAGE_SIZE).then(
      (page) =>
        setState((s) => ({
          trace: page,
          events: append ? [...s.events, ...page.events] : page.events,
          next: page.page?.next_after_sequence ?? null,
          start: append ? s.start : after,
          error: null,
          loading: false,
        })),
      (error: unknown) => setState((s) => ({ ...s, loading: false, error: String(error) })),
    );
  };

  useEffect(() => {
    let cancelled = false;
    const start = eventId
      ? api.traceEvent(runId, eventId).then((event) => cursorBefore(event.sequence))
      : Promise.resolve(null);
    start.then(
      (after) => {
        if (!cancelled) load(after, false);
      },
      (error: unknown) => {
        if (!cancelled) setState((s) => ({ ...s, loading: false, error: String(error) }));
      },
    );
    return () => {
      cancelled = true;
    };
    // `load` depende sólo de runId; recargar al cambiar de run o de evento citado.
  }, [runId, eventId]);

  useEffect(() => {
    if (eventId) document.getElementById(`event-${eventId}`)?.scrollIntoView({ block: "center" });
  }, [eventId, state.events]);

  return (
    <>
      {state.trace ? <TraceSummary trace={state.trace} /> : null}
      {state.error ? (
        <p className="error" role="alert">
          {state.error}
        </p>
      ) : null}
      {state.start !== null ? (
        <p>
          Mostrando desde el evento {state.start + 1}.{" "}
          <button type="button" onClick={() => load(null, false)}>
            Ver desde el principio
          </button>
        </p>
      ) : null}
      <TraceEvents events={state.events} highlight={eventId} />
      {state.loading ? <p className="muted">Cargando eventos…</p> : null}
      {!state.loading && state.next !== null ? (
        <button type="button" onClick={() => load(state.next, true)}>
          Cargar {TRACE_PAGE_SIZE} eventos más
        </button>
      ) : null}
      {!state.loading && state.trace && state.next === null ? (
        <p className="muted">Fin de la traza ({state.trace.event_count} eventos sellados).</p>
      ) : null}
    </>
  );
}

export function RunView({ id, eventId }: { id: string; eventId: string | null }) {
  const run = useAsync((signal) => api.run(id, signal), [id]);
  const evaluations = useAsync((signal) => api.evaluations(id, signal), [id]);
  return (
    <section>
      <h2>Run {id.slice(0, 8)}</h2>
      <Loadable result={run}>{(data) => <RunHeader run={data} />}</Loadable>
      <h3>Scores (última evaluación)</h3>
      <Loadable result={evaluations}>
        {(data) => <EvaluationPanel runId={id} evaluations={data} />}
      </Loadable>
      <h3>Traza</h3>
      {run.state === "ready" && run.data.status === "queued" ? (
        <p className="muted">Run en cola: todavía no tiene traza.</p>
      ) : (
        <TracePanel runId={id} eventId={eventId} />
      )}
    </section>
  );
}
