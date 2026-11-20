import { STATES, type Plan } from "./types";

function repoName(plan: Plan): string {
  return plan.gh_repo ?? plan.repo_dir?.split("/").pop() ?? "";
}

function PlanLink({ plan, selected }: { plan: Plan; selected: boolean }) {
  return (
    <a
      href={`#/plan/${encodeURIComponent(plan.id)}`}
      className={`plan-link${selected ? " selected" : ""}${plan.missing ? " missing" : ""}`}
      title={plan.missing ? "Spec file is missing" : plan.path}
    >
      <span className="plan-title">{plan.title}</span>
      <span className="plan-meta">
        <span className="repo">{repoName(plan)}</span>
        {STATES.filter((s) => plan.counts[s]).map((s) => (
          <span key={s} className={`count status-${s}`} title={s}>
            {plan.counts[s]}
          </span>
        ))}
      </span>
    </a>
  );
}

export default function Sidebar({ plans, selectedId }: { plans: Plan[]; selectedId: string | null }) {
  const groups: [string, Plan[]][] = [
    ["Active", plans.filter((p) => !p.archived && !p.done)],
    ["Done", plans.filter((p) => !p.archived && p.done)],
    ["Archived", plans.filter((p) => p.archived)],
  ];
  return (
    <nav className="sidebar">
      {groups.map(
        ([name, group]) =>
          group.length > 0 && (
            <section key={name}>
              <h2>{name}</h2>
              {group.map((p) => (
                <PlanLink key={p.id} plan={p} selected={p.id === selectedId} />
              ))}
            </section>
          ),
      )}
      {plans.length === 0 && <p className="muted">No plans yet.</p>}
    </nav>
  );
}
