import type { PR, PRDetail, Plan, PlanDetail, Status } from "./types";

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body ? { "content-type": "application/json" } : undefined,
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(body?.error ?? `${res.status} ${res.statusText}`);
  return body as T;
}

const id = encodeURIComponent;

export const api = {
  plans: () => request<Plan[]>("/api/plans"),
  plan: (planId: string) => request<PlanDetail>(`/api/plans/${id(planId)}`),
  pr: (prId: string) => request<PRDetail>(`/api/prs/${id(prId)}`),
  patchPr: (prId: string, body: { status?: Status; url?: string | null; force?: boolean }) =>
    request<PR>(`/api/prs/${id(prId)}`, {
      method: "PATCH",
      body: JSON.stringify({ ...body, source: "human" }),
    }),
  sync: () => request<unknown>("/api/sync", { method: "POST" }),
};
