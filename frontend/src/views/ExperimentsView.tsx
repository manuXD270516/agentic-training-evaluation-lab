import { api } from "../api/client";
import type { Experiment } from "../api/types";
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

export function ExperimentsView() {
  const result = useAsync((signal) => api.experiments(signal), []);
  return (
    <section>
      <h2>Experimentos</h2>
      <Loadable result={result}>{(data) => <ExperimentsTable experiments={data} />}</Loadable>
    </section>
  );
}
