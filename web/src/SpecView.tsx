import type { PlanDetail } from "./types";

// The rendered spec in an iframe, so the `mth` page's CSS and scripts stay isolated
// (the server also sandboxes it with a CSP header).
export default function SpecView({ plan }: { plan: PlanDetail }) {
  return (
    <>
      <div className="spec-bar">
        <a href={`#/plan/${encodeURIComponent(plan.id)}`}>Back to PRs</a>
        {plan.spec_url && (
          <a href={plan.spec_url} target="_blank" rel="noreferrer">
            Open in new tab
          </a>
        )}
        <span className="muted">{plan.title}</span>
      </div>
      {plan.spec_url ? (
        <iframe className="spec-frame" src={plan.spec_url} title={`${plan.title} (original plan)`} />
      ) : (
        <p className="muted spec-bar">This plan has not rendered yet.</p>
      )}
    </>
  );
}
