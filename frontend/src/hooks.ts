import { useEffect, useState } from "react";

export type Async<T> =
  { state: "loading" } | { state: "error"; error: Error } | { state: "ready"; data: T };

/** Carga `load` cuando cambian `deps`; descarta respuestas de cargas ya obsoletas. */
export function useAsync<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): Async<T> {
  const [result, setResult] = useState<Async<T>>({ state: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    setResult({ state: "loading" });
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ state: "ready", data });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setResult({
            state: "error",
            error: error instanceof Error ? error : new Error(String(error)),
          });
        }
      },
    );
    return () => controller.abort();
    // `load` se recrea en cada render; las dependencias reales las declara quien llama.
  }, deps);
  return result;
}

export function useHash(): string {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const update = () => setHash(window.location.hash);
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return hash;
}
