import { useHash } from "./hooks";
import { href, parseRoute } from "./model";
import { ExperimentView } from "./views/ExperimentView";
import { ExperimentsView } from "./views/ExperimentsView";
import { RunView } from "./views/RunView";

function Page() {
  const route = parseRoute(useHash());
  switch (route.view) {
    case "experiments":
      return <ExperimentsView />;
    case "experiment":
      return <ExperimentView key={`${route.id}:${route.mode}`} id={route.id} mode={route.mode} />;
    case "run":
      return <RunView id={route.id} eventId={route.eventId} />;
    case "not_found":
      return (
        <p>
          Ruta desconocida <code>{route.hash}</code>. <a href={href.experiments()}>Volver</a>
        </p>
      );
  }
}

export function App() {
  return (
    <main>
      <header>
        <h1>
          <a href={href.experiments()}>evallab</a>
        </h1>
        <p className="muted">
          Dashboard de sólo lectura. Los resultados scripted o de fixture prueban el harness, no un
          modelo; cada agente muestra su atribución.
        </p>
      </header>
      <Page />
    </main>
  );
}
