import Markdown from "react-markdown";
import PrRow from "./PrRow";
import type { PlanDetail } from "./types";

export default function PlanView({ plan, onChange }: { plan: PlanDetail; onChange: () => void }) {
  const byId = new Map(plan.prs.map((p) => [p.id, p]));
  const active = plan.prs.filter((p) => !p.removed);
  const removed = plan.prs.filter((p) => p.removed);
  const rows = (prs: typeof plan.prs) =>
    prs.map((pr) => <PrRow key={pr.id} pr={pr} byId={byId} onChange={onChange} />);

  return (
    <div className={`plan-view${plan.missing ? " missing" : ""}`}>
      <h2>{plan.title}</h2>
      <p className="path">
        {plan.path}
        {plan.missing && <span className="error"> (file missing)</span>}
      </p>
      {plan.notes_md && (
        <div className="notes">
          <Markdown>{plan.notes_md}</Markdown>
        </div>
      )}
      <div className="prs">{rows(active)}</div>
      {removed.length > 0 && (
        <details className="removed">
          <summary>Removed from plan ({removed.length})</summary>
          <div className="prs">{rows(removed)}</div>
        </details>
      )}
    </div>
  );
}
