import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { api } from "./api";
import StatusPill from "./StatusPill";
import type { PR, PrEvent } from "./types";

function UrlField({ pr, onChange }: { pr: PR; onChange: () => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const url = draft!.trim() || null;
    setError(null);
    api.patchPr(pr.id, { url }).then(
      () => (setDraft(null), onChange()),
      (e) => setError(e.message),
    );
  };

  if (draft === null) {
    return (
      <span className="url">
        {/* Only link http(s) URLs so a stored `javascript:` URL cannot run on click. */}
        {pr.url && /^https?:\/\//i.test(pr.url) ? (
          <a href={pr.url} target="_blank" rel="noreferrer">
            {pr.url.match(/\/pull\/(\d+)/)?.[0].replace("/pull/", "#") ?? pr.url}
          </a>
        ) : pr.url ? (
          <span>{pr.url}</span>
        ) : (
          <span className="muted">no PR</span>
        )}
        <button className="link" onClick={() => setDraft(pr.url ?? "")}>
          edit
        </button>
      </span>
    );
  }
  return (
    <span className="url">
      <input
        autoFocus
        value={draft}
        placeholder="https://github.com/owner/repo/pull/1"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => (e.key === "Enter" ? save() : e.key === "Escape" && setDraft(null))}
      />
      <button onClick={save}>Save</button>
      <button className="link" onClick={() => setDraft(null)}>
        cancel
      </button>
      {error && <span className="error">{error}</span>}
    </span>
  );
}

function History({ pr }: { pr: PR }) {
  const [events, setEvents] = useState<PrEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Refetch whenever the PR changes so history follows live updates.
  useEffect(() => {
    let current = true;
    api.pr(pr.id).then(
      (d) => current && setEvents(d.events),
      (e) => current && setError(e.message),
    );
    return () => {
      current = false;
    };
  }, [pr.id, pr.updated_at]);

  if (error) return <p className="error">{error}</p>;
  if (!events) return null;
  if (events.length === 0) return <p className="muted">No status changes yet.</p>;
  return (
    <ul className="history">
      {events.map((e) => (
        <li key={e.id}>
          <time>{new Date(e.at).toLocaleString()}</time> {e.from_status} &rarr; {e.to_status}{" "}
          <span className="muted">
            by {e.source}
            {e.actor && ` (${e.actor})`}
            {e.forced ? ", forced" : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function PrRow({
  pr,
  byId,
  onChange,
}: {
  pr: PR;
  byId: Map<string, PR>;
  onChange: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="pr">
      <div className="pr-line">
        <button className="toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "\u25be" : "\u25b8"}
        </button>
        <span className="number">{pr.number}</span>
        <span className="title" onClick={() => setOpen(!open)}>
          {pr.title}
          {pr.ready && <span className="ready">ready</span>}
        </span>
        <StatusPill pr={pr} onChange={onChange} />
        <UrlField pr={pr} onChange={onChange} />
        <span className="branch" title="branch">
          {pr.branch && <code>{pr.branch}</code>}
        </span>
        <span className="deps">
          {pr.depends_on.map((d) => {
            const dep = byId.get(d);
            return (
              <span
                key={d}
                className={`chip status-${dep?.status ?? "unknown"}`}
                title={dep ? `${dep.title}: ${dep.status}` : d}
              >
                {dep?.number ?? d}
              </span>
            );
          })}
        </span>
      </div>
      {open && (
        <div className="pr-detail">
          {pr.scope_md ? <Markdown>{pr.scope_md}</Markdown> : <p className="muted">No scope.</p>}
          <History pr={pr} />
        </div>
      )}
    </div>
  );
}
