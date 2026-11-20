// pi extension: keeps pr-tracker statuses current from agent activity.
// Load in place: add "~/GitHub/pr-tracker/pi" to "extensions" in ~/.pi/agent/settings.json.
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

import { hasPlanHeading, parseBranch, parseImplementInput, parsePrUrl } from "./hooks.ts";

const BASE_URL = (process.env.PRT_URL ?? "http://127.0.0.1:4747").replace(/\/$/, "");
const ROOT = join(homedir(), "GitHub");
const ENTRY_TYPE = "prt-pr";

function expand(path: string, cwd: string): string {
  return resolve(cwd, path === "~" || path.startsWith("~/") ? homedir() + path.slice(1) : path);
}

export default function (pi: ExtensionAPI) {
  let prId: string | undefined;
  let warned = false;
  // Requests run in order without blocking hooks, so Implementing lands before branch and In-review.
  let queue: Promise<void> = Promise.resolve();

  const enqueue = (task: () => Promise<void>) => {
    queue = queue.then(task).catch(() => {});
  };

  // The response, or undefined when the server is unreachable (warned once per session).
  const api = async (
    ctx: ExtensionContext,
    method: string,
    path: string,
    body?: object,
  ): Promise<Response | undefined> => {
    try {
      return await fetch(BASE_URL + path, {
        method,
        headers: body ? { "content-type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(2000),
      });
    } catch {
      if (!warned) {
        warned = true;
        ctx.ui.notify(`pr-tracker unreachable at ${BASE_URL}; PR status is not being updated`, "warning");
      }
      return undefined;
    }
  };

  // The response is not inspected: a 409 (e.g. already past Implementing) is expected.
  const patchPr = async (ctx: ExtensionContext, fields: object) => {
    if (prId) {
      await api(ctx, "PATCH", `/api/prs/${encodeURIComponent(prId)}`, { ...fields, source: "agent" });
    }
  };

  const startImplementing = async (ctx: ExtensionContext, path: string, number: string) => {
    const plan = await api(ctx, "POST", "/api/plans", { path });
    if (!plan) return;
    if (!plan.ok) {
      ctx.ui.notify(`pr-tracker: could not register ${path} (${plan.status})`, "warning");
      return;
    }
    const query = new URLSearchParams({ path, number });
    const pr = await api(ctx, "GET", `/api/prs?${query}`);
    if (!pr) return;
    if (!pr.ok) {
      ctx.ui.notify(`pr-tracker: PR ${number} not found in ${path}`, "warning");
      return;
    }
    prId = ((await pr.json()) as { id: string }).id;
    pi.appendEntry(ENTRY_TYPE, { id: prId });
    await patchPr(ctx, { status: "Implementing" });
  };

  pi.on("session_start", (_event, ctx) => {
    warned = false;
    prId = undefined;
    for (const entry of ctx.sessionManager.getBranch()) {
      if (entry.type === "custom" && entry.customType === ENTRY_TYPE) {
        prId = (entry.data as { id: string }).id;
      }
    }
  });

  // Let queued requests finish (each is bounded by the 2 s timeout) before pi exits.
  pi.on("session_shutdown", () => queue);

  pi.on("input", (event, ctx) => {
    const match = parseImplementInput(event.text);
    if (match) enqueue(() => startImplementing(ctx, expand(match.spec, ctx.cwd), match.number));
    return { action: "continue" };
  });

  pi.on("tool_result", (event, ctx) => {
    if (event.isError) return;
    if (event.toolName === "bash") {
      const command = String(event.input.command ?? "");
      const output = event.content.map((c) => (c.type === "text" ? c.text : "")).join("\n");
      const branch = parseBranch(command);
      if (branch) enqueue(() => patchPr(ctx, { branch }));
      const url = parsePrUrl(command, output);
      if (url) enqueue(() => patchPr(ctx, { url, status: "In-review" }));
    } else if (event.toolName === "write" || event.toolName === "edit") {
      const path = expand(String(event.input.path ?? ""), ctx.cwd);
      if (!path.endsWith(".md") || !path.startsWith(ROOT + "/")) return;
      const written = event.toolName === "write" ? String(event.input.content ?? "") : undefined;
      enqueue(async () => {
        if (hasPlanHeading(written ?? (await readFile(path, "utf8")))) {
          await api(ctx, "POST", "/api/plans", { path });
        }
      });
    }
  });
}
