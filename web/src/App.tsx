import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ModeContext } from "./mode";
import PlanView from "./PlanView";
import Sidebar from "./Sidebar";
import type { Mode, Plan, PlanDetail } from "./types";

// `#/plan/<id>`; a trailing `/spec` (original plan view) is ignored for now.
function planIdFromHash(): string | null {
  const m = window.location.hash.match(/^#\/plan\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

export default function App() {
  const [mode, setMode] = useState<Mode | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [selectedId, setSelectedId] = useState(planIdFromHash);
  const [plan, setPlan] = useState<PlanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;

  const loadPlans = useCallback(() => {
    api.plans().then(setPlans, (e) => setError(e.message));
  }, []);

  const loadPlan = useCallback((planId: string | null) => {
    if (!planId) return setPlan(null);
    api.plan(planId).then(
      // Drop responses that arrive after the user switched plans.
      (p) => selectedRef.current === planId && (setPlan(p), setError(null)),
      (e) => selectedRef.current === planId && (setPlan(null), setError(e.message)),
    );
  }, []);

  useEffect(() => {
    const onHash = () => setSelectedId(planIdFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => loadPlan(selectedId), [selectedId, loadPlan]);

  useEffect(() => {
    const es = new EventSource("/api/events");
    // Fires on the first connect and every reconnect, so state never goes stale.
    es.onopen = () => {
      setConnected(true);
      api.mode().then(setMode, (e) => setError(e.message));
      loadPlans();
      loadPlan(selectedRef.current);
    };
    es.onerror = () => setConnected(false);
    es.onmessage = (e) => {
      const { plan_id } = JSON.parse(e.data) as { plan_id: string };
      loadPlans();
      if (plan_id === selectedRef.current) loadPlan(plan_id);
    };
    return () => es.close();
  }, [loadPlans, loadPlan]);

  const sync = () => {
    setSyncing(true);
    setSyncError(null);
    api
      .sync()
      .catch((e) => setSyncError(`Sync failed: ${e.message}`))
      .finally(() => setSyncing(false));
  };

  return (
    <div className="app">
      <header>
        <h1>PR tracker</h1>
        <div className="header-right">
          {syncError && <span className="error">{syncError}</span>}
          {mode === "full" && (
            <button onClick={sync} disabled={syncing}>
              {syncing ? "Syncing..." : "Sync GitHub"}
            </button>
          )}
          <span className={`conn ${connected ? "on" : "off"}`}>
            {connected ? "Live" : "Disconnected"}
          </span>
        </div>
      </header>
      <Sidebar plans={plans} selectedId={selectedId} />
      <main>
        {error && <p className="error">{error}</p>}
        {plan && mode ? (
          <ModeContext value={mode}>
            <PlanView plan={plan} onChange={() => (loadPlans(), loadPlan(plan.id))} />
          </ModeContext>
        ) : (
          !error && <p className="muted">Select a plan.</p>
        )}
      </main>
    </div>
  );
}
