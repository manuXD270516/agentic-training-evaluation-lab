import type { ReactNode } from "react";
import type { ScoreStatus } from "./api/types";
import type { Async } from "./hooks";
import { statusDisplay } from "./model";

export function StatusBadge({ status }: { status: ScoreStatus | null }) {
  const display = statusDisplay(status);
  return (
    <span className={`badge badge-${display.key}`} title={display.title}>
      {display.label}
    </span>
  );
}

export function Hash({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return (
    <code className="hash" title={value}>
      {value.slice(0, 12)}…
    </code>
  );
}

export function Loadable<T>({
  result,
  children,
}: {
  result: Async<T>;
  children: (data: T) => ReactNode;
}) {
  if (result.state === "loading") return <p className="muted">Cargando…</p>;
  if (result.state === "error") {
    return (
      <p className="error" role="alert">
        Error: {result.error.message}
      </p>
    );
  }
  return <>{children(result.data)}</>;
}

export function Legend() {
  const keys: (ScoreStatus | null)[] = ["pass", "fail", "unknown", "not_applicable", "error", null];
  return (
    <p className="legend">
      {keys.map((key) => (
        <span key={key ?? "none"}>
          <StatusBadge status={key} /> {statusDisplay(key).title}
        </span>
      ))}
    </p>
  );
}
