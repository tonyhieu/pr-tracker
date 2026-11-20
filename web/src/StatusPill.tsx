import { useContext, useState } from "react";
import { api } from "./api";
import { ModeContext } from "./mode";
import { STATES, type PR, type Status } from "./types";

const FORCE = "__force__";

export default function StatusPill({ pr, onChange }: { pr: PR; onChange: () => void }) {
  const [forcing, setForcing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mode = useContext(ModeContext);

  const patch = (status: Status, force: boolean) => {
    setError(null);
    // Refetch on failure too: a 409 means the shown status was stale.
    api.patchPr(pr.id, { status, force }, mode).then(onChange, (e) => (setError(e.message), onChange()));
  };

  const pick = (value: string) => {
    if (value === FORCE) return setForcing(true);
    if (value !== pr.status) patch(value as Status, false);
  };

  const pickForced = (value: string) => {
    setForcing(false);
    const to = value as Status;
    if (to !== pr.status && window.confirm(`Force ${pr.title} from ${pr.status} to ${to}?`)) {
      patch(to, true);
    }
  };

  return (
    <span className="status">
      {forcing ? (
        <select
          autoFocus
          className={`pill status-${pr.status}`}
          value=""
          onChange={(e) => pickForced(e.target.value)}
          onBlur={() => setForcing(false)}
        >
          <option value="" disabled>
            Force to...
          </option>
          {STATES.filter((s) => s !== pr.status).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      ) : (
        <select
          className={`pill status-${pr.status}`}
          value={pr.status}
          onChange={(e) => pick(e.target.value)}
        >
          {[pr.status, ...pr.allowed].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
          <option value={FORCE}>force...</option>
        </select>
      )}
      {error && <span className="error">{error}</span>}
    </span>
  );
}
