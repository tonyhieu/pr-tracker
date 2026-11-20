export const STATES = ["TBD", "Implementing", "In-review", "Merged", "Abandoned"] as const;
export type Status = (typeof STATES)[number];

// `viewer` is the restricted tailnet app: status changes only, no sync or URL edits.
export type Mode = "full" | "viewer";

export interface Plan {
  id: string;
  path: string;
  repo_dir: string | null;
  gh_repo: string | null;
  title: string;
  notes_md: string | null;
  missing: boolean;
  archived: boolean;
  counts: Record<Status, number>;
  done: boolean;
}

export interface PR {
  id: string;
  plan_id: string;
  number: string;
  title: string;
  scope_md: string | null;
  depends_on: string[];
  status: Status;
  url: string | null;
  branch: string | null;
  removed: boolean;
  updated_at: string | null;
  allowed: Status[];
  ready: boolean;
}

export interface PlanDetail extends Plan {
  prs: PR[];
}

export interface PrEvent {
  id: number;
  from_status: Status;
  to_status: Status;
  source: "human" | "agent" | "github";
  actor: string | null;
  forced: number;
  at: string;
}

export interface PRDetail extends PR {
  events: PrEvent[];
}
