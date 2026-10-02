import { api, rawLinks } from "../api/client";
import type { ComparisonSummary, Experiment, Snapshot } from "../api/types";
import { Hash, Loadable } from "../components";
import { useAsync } from "../hooks";
import { href } from "../model";

export function ExperimentsTable({ experiments }: { experiments: Experiment[] }) {
  if (experiments.length === 0) {
    return (
      <p className="muted">
        No hay experimentos. Se crean con <code>evallab-benchmark run</code> o la API.
      </p>
    );
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Hipótesis</th>
          <th>Estado</th>
          <th>Agentes</th>
          <th>Repeticiones</th>
          <th>Manifest</th>
          <th>Creado (UTC)</th>
        </tr>
      </thead>
      <tbody>
        {experiments.map((exp) => (
          <tr key={exp.id}>
            <td>
              {exp.manifest_hash ? (
                <a href={href.experiment(exp.id)}>{exp.hypothesis}</a>
              ) : (
                <span title="Sin sellar: no tiene reporte">{exp.hypothesis}</span>
              )}
            </td>
            <td>{exp.status}</td>
            <td>{exp.agents.length}</td>
            <td>{exp.repetitions}</td>
            <td>
              <Hash value={exp.manifest_hash} />
            </td>
            <td>{exp.created_at.slice(0, 19).replace("T", " ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ComparisonsTable({ comparisons }: { comparisons: ComparisonSummary[] }) {
  if (comparisons.length === 0) return null;
  return (
    <>
      <h3>Comparaciones controladas exportadas</h3>
      <table>
        <thead>
          <tr>
            <th>Baseline</th>
            <th>Candidato</th>
            <th>Estado</th>
            <th>Decisión</th>
            <th>Export</th>
          </tr>
        </thead>
        <tbody>
          {comparisons.map((c) => (
            <tr key={c.id}>
              <td>
                <a href={href.experiment(c.baseline)}>{c.baseline.slice(0, 8)}</a>
              </td>
              <td>
                <a href={href.experiment(c.candidate)}>{c.candidate.slice(0, 8)}</a>
              </td>
              <td>{c.status}</td>
              <td>
                <strong>{c.decision.status}</strong>{" "}
                <span className="muted">({c.decision.reasons.join(", ")})</span>
              </td>
              <td>
                <a href={rawLinks.comparison(c.id)}>
                  JSON <Hash value={c.export_digest} />
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

const RESULTS_URL =
  "https://github.com/manuXD270516/agentic-training-evaluation-lab/tree/main/results";

export function SnapshotNote({ snapshot }: { snapshot: Snapshot | null }) {
  if (!snapshot) return null;
  return (
    <p className="banner-warn">
      Instantánea estática (sin backend, sólo lectura) generada el{" "}
      {snapshot.generated_at.slice(0, 19).replace("T", " ")} UTC
      {snapshot.commit ? ` desde el commit ${snapshot.commit.slice(0, 7)}` : ""}. {snapshot.note}{" "}
      <a href={RESULTS_URL}>Resultados registrados</a>.
    </p>
  );
}

export function ExperimentsView() {
  const result = useAsync(async (signal) => {
    const [experiments, comparisons, snapshot] = await Promise.all([
      api.experiments(signal),
      api.comparisons(),
      api.snapshot(),
    ]);
    return { experiments, comparisons, snapshot };
  }, []);
  return (
    <section>
      <Loadable result={result}>
        {(data) => (
          <>
            <SnapshotNote snapshot={data.snapshot} />
            <h2>Experimentos</h2>
            <ExperimentsTable experiments={data.experiments} />
            <ComparisonsTable comparisons={data.comparisons} />
          </>
        )}
      </Loadable>
    </section>
  );
}
