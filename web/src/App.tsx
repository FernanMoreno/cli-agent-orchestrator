import { useEffect, useState, useSyncExternalStore, Suspense } from "react";
import { api } from "./api";
import { useStore } from "./store";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { DashboardHome } from "./components/DashboardHome";
import { AgentPanel } from "./components/AgentPanel";
import { FlowsPanel } from "./components/FlowsPanel";
import { MemoryPanel } from "./components/MemoryPanel";
import { ProfilesPanel } from "./components/ProfilesPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { WorkflowsPanel } from "./components/WorkflowsPanel";
import { BeadsPanel } from "./components/BeadsPanel";
import { RalphPanel } from "./components/RalphPanel";
import { PluginsPanel } from "./components/PluginsPanel";
import { PLUGINS_TAB_ENABLED } from "./featureFlags";
import { NodeSwitcher } from "./components/NodeSwitcher";
import { CaoMark } from "./components/CaoMark";
import {
  authSnapshot,
  initializeBrowserAuth,
  logoutBrowser,
  subscribeBrowserAuth,
} from "./auth";
import { BrowserAccess } from "./components/BrowserAccess";
import { BrowserAccount } from "./components/BrowserAccount";
import { BrowserConnection } from "./components/BrowserConnection";
import {
  Bot,
  Home,
  Clock,
  Settings,
  Brain,
  Workflow,
  Puzzle,
  CheckCircle,
  XCircle,
  Info,
  Wifi,
  WifiOff,
  Package,
} from "lucide-react";

type TabKey =
  | "home"
  | "profiles"
  | "agents"
  | "flows"
  | "settings"
  | "memory"
  | "workflows"
  | "plugins"
  | "beads"
  | "ralph";

// Profiles sits between Home and Agents (#510): browsing/authoring profiles
// precedes launching agents, and AgentPanel stays the launch picker. This was
// a one-time Alt+N renumbering of the tabs after it; Workflows + Memory remain
// appended last (Memory is conditional, so keeping it last stops the numbering
// of the always-visible tabs shifting with the memory backend's status).
//
// Plugins is appended after them, always. Alt+N numbers the VISIBLE tabs in
// array order, so inserting anywhere but the end renumbers every shortcut after
// the insertion point — which is why Memory, then Workflows, then Plugins each
// went last rather than into a "logical" slot. (Agent Plugins, not the
// event-plugin system — see docs/agent-plugins.md.)
const TABS: { key: TabKey; label: string; icon: React.ReactNode }[] = [
  { key: "home", label: "Home", icon: <Home size={16} /> },
  { key: "profiles", label: "Profiles", icon: <Package size={16} /> },
  { key: "agents", label: "Agents", icon: <Bot size={16} /> },
  { key: "flows", label: "Flows", icon: <Clock size={16} /> },
  { key: "settings", label: "Settings", icon: <Settings size={16} /> },
  { key: "memory", label: "Memory", icon: <Brain size={16} /> },
  { key: "workflows", label: "Workflows", icon: <Workflow size={16} /> },
  { key: "plugins", label: "Plugins", icon: <Puzzle size={16} /> },
  { key: "beads", label: "Tasks", icon: <CheckCircle size={16} /> },
  { key: "ralph", label: "Ralph", icon: <Workflow size={16} /> },
];

function Snackbar() {
  const { snackbar, hideSnackbar } = useStore();

  useEffect(() => {
    if (snackbar) {
      const timer = setTimeout(hideSnackbar, 3000);
      return () => clearTimeout(timer);
    }
  }, [snackbar, hideSnackbar]);

  if (!snackbar) return null;

  const colors = {
    success: "bg-emerald-600 border-emerald-500",
    error: "bg-red-600 border-red-500",
    info: "bg-blue-600 border-blue-500",
  };
  const icons = {
    success: <CheckCircle size={18} />,
    error: <XCircle size={18} />,
    info: <Info size={18} />,
  };

  return (
    <div
      role="alert"
      className={`fixed bottom-4 right-4 z-50 px-4 py-3 rounded-lg border shadow-lg flex items-center gap-2 text-white ${colors[snackbar.type]}`}
    >
      {icons[snackbar.type]}
      <span className="text-sm">{snackbar.message}</span>
    </div>
  );
}

function Dashboard() {
  const [tab, setTab] = useState<TabKey>("home");

  // The ONLY way any surface changes tabs. Refuses while an in-flight
  // interaction (an authoring modal's save) holds the navigation lock:
  // switching tabs unmounts the panel and its modal, so a deferred
  // validation/write rejection would land on an unmounted component and the
  // unsaved draft would be unrecoverable (#692 review round 7). Reads the
  // lock through getState() so the keydown listener never closes over a
  // stale value.
  const requestTabChange = (t: TabKey) => {
    if (useStore.getState().navLockCount > 0) return;
    setTab(t);
  };
  // Default false (fail-closed): a dead backend hides the tab rather than showing a broken panel
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const {
    sessions,
    connected,
    fetchSessions,
    fleetMode,
    fleetError: registryError,
    refreshFleet,
  } = useStore();
  // Subscribed (not just read via getState) so the tab strip re-renders
  // and visibly reflects the refusal while a save owns navigation --
  // matching the modal's own disabled Close/Cancel/mode-tab affordances
  // rather than silently ignoring clicks.
  const navLocked = useStore((s) => s.navLockCount > 0);

  // Two independent gates, both fail-closed. Memory is a runtime capability
  // check; Plugins is the build-time M1 gate (Requirement 16.5) and mirrors
  // `hidden=True` on the Click group and `Policy::Hidden` on the TUI rows.
  const visibleTabs = TABS.filter(
    (t) =>
      (t.key !== "memory" || memoryEnabled) &&
      (t.key !== "plugins" || PLUGINS_TAB_ENABLED),
  );

  useEffect(() => {
    fetchSessions();
    api
      .getMemoryStatus()
      .then((s) => setMemoryEnabled(s.enabled))
      .catch(() => {});
    const interval = setInterval(fetchSessions, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!fleetMode) return;
    const interval = setInterval(() => {
      void refreshFleet();
    }, 10000);
    return () => {
      clearInterval(interval);
      useStore.getState().stopFleetRefresh();
    };
  }, [fleetMode, refreshFleet]);

  // The nav lock stops IN-APP navigation from unmounting an in-flight
  // save, but browser chrome (reload, tab close) bypasses it entirely --
  // the same draft-loss path one level up. While the lock is held, ask
  // the browser to confirm leaving.
  useEffect(() => {
    if (!navLocked) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [navLocked]);

  // Keyboard shortcuts: Alt+1-N over the visible tabs
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey && e.key >= "1" && e.key <= String(visibleTabs.length)) {
        e.preventDefault();
        requestTabChange(visibleTabs[parseInt(e.key) - 1].key);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [memoryEnabled]);

  return (
    <div className="min-h-screen bg-[#0f0f14] text-gray-200">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <CaoMark size={32} />
            <h1 className="text-lg font-bold text-white">
              CLI Agent Orchestrator
            </h1>
          </div>
          <div className="flex items-center gap-4">
            <NodeSwitcher />
            {authSnapshot().mode === "local_password" ? (
              <BrowserAccount />
            ) : (
              <BrowserConnection />
            )}
            <span className="text-xs text-gray-500">
              {sessions.length} session{sessions.length !== 1 ? "s" : ""}
            </span>
            <div
              className="flex items-center gap-1.5"
              title={connected ? "Connected" : "Disconnected"}
            >
              {connected ? (
                <Wifi size={14} className="text-emerald-400" />
              ) : (
                <WifiOff size={14} className="text-red-400" />
              )}
              <span
                className={`text-xs ${connected ? "text-emerald-400" : "text-red-400"}`}
              >
                {connected ? "Live" : "Offline"}
              </span>
            </div>
          </div>
        </div>
      </header>

      {registryError && (
        <div
          role="alert"
          className="max-w-7xl mx-auto px-6 py-2 text-amber-300"
        >
          Fleet registry: {registryError}
        </div>
      )}
      {/* Tab Bar */}
      <div className="border-b border-gray-800">
        <div className="max-w-7xl mx-auto px-6">
          <nav className="flex gap-1 py-2" role="tablist">
            {visibleTabs.map((t, i) => (
              <button
                key={t.key}
                role="tab"
                aria-selected={tab === t.key}
                aria-disabled={navLocked && tab !== t.key}
                onClick={() => requestTabChange(t.key)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 flex items-center gap-2 ${
                  tab === t.key
                    ? "bg-gradient-to-r from-emerald-600 to-emerald-500 text-white shadow-lg shadow-emerald-500/20"
                    : navLocked
                      ? "text-gray-600 cursor-not-allowed"
                      : "text-gray-400 hover:text-white hover:bg-gray-800/50"
                }`}
                title={
                  navLocked && tab !== t.key
                    ? "Finish or cancel the in-flight save first"
                    : `Alt+${i + 1}`
                }
              >
                {t.icon}
                {t.label}
                {t.key === "agents" && sessions.length > 0 && (
                  <span
                    className={`px-1.5 py-0.5 text-xs rounded-full ${tab === t.key ? "bg-white/20" : "bg-gray-700"}`}
                  >
                    {sessions.length}
                  </span>
                )}
              </button>
            ))}
          </nav>
        </div>
      </div>

      {/* Content */}
      <main className="max-w-7xl mx-auto px-6 py-6">
        <ErrorBoundary>
          <Suspense
            fallback={
              <div className="text-gray-500 text-sm py-12 text-center">
                Loading...
              </div>
            }
          >
            {tab === "home" && (
              <DashboardHome
                onNavigate={(t) => requestTabChange(t as TabKey)}
              />
            )}
            {tab === "profiles" && <ProfilesPanel />}
            {tab === "agents" && <AgentPanel />}
            {tab === "flows" && <FlowsPanel />}
            {tab === "settings" && <SettingsPanel />}
            {tab === "memory" && <MemoryPanel />}
            {tab === "workflows" && <WorkflowsPanel />}
            {tab === "beads" && <BeadsPanel />}
            {tab === "ralph" && <RalphPanel />}
            {tab === "plugins" && PLUGINS_TAB_ENABLED && <PluginsPanel />}
          </Suspense>
        </ErrorBoundary>
      </main>

      <Snackbar />
    </div>
  );
}

export default function App() {
  const auth = useSyncExternalStore(subscribeBrowserAuth, authSnapshot);
  const [fleetReady, setFleetReady] = useState(false);
  const [fleetError, setFleetError] = useState<string | null>(null);
  const nodeEpoch = useStore((s) => s.nodeEpoch);
  useEffect(() => {
    void initializeBrowserAuth();
  }, []);
  const setupAvailable =
    auth.mode === "bearer" && auth.config?.local_setup_available === true;
  const principalAvailable =
    !auth.logoutPending &&
    !setupAvailable &&
    auth.mode !== null &&
    (auth.mode === "local_password"
      ? auth.session !== null
      : auth.status === "authenticated");
  useEffect(() => {
    let current = true;
    setFleetReady(false);
    setFleetError(null);
    if (!principalAvailable)
      return () => {
        current = false;
      };
    void api
      .getFleet()
      .then((fleet) => {
        if (!current) return;
        if (!fleet.machines.length)
          throw new Error("Fleet registry contains no nodes");
        useStore.setState({
          fleetNodes: fleet.machines,
          fleetMode: true,
          fleetError: null,
        });
        useStore
          .getState()
          .selectNode(
            (
              fleet.machines.find(
                (node) =>
                  node.online &&
                  ["supervisor", "central"].includes(node.role ?? ""),
              ) ??
              fleet.machines.find((node) => node.online) ??
              fleet.machines[0]
            ).name,
          );
        setFleetReady(true);
        setFleetReady(true);
      })
      .catch((error: any) => {
        if (!current) return;
        if (error?.status === 404) {
          useStore.setState({
            fleetNodes: [],
            fleetMode: false,
            fleetError: null,
          });
          useStore.getState().selectNode(null);
          setFleetReady(true);
          setFleetReady(true);
        } else setFleetError(error?.message ?? "Fleet registry unavailable");
      });
    return () => {
      current = false;
    };
  }, [principalAvailable, auth.mode, auth.session?.session_id]);
  if (auth.status === "initializing")
    return <main role="status">Comprobando sesión…</main>;
  if (fleetError)
    return (
      <main role="alert">
        {fleetError}
        <button onClick={() => location.reload()}>Reintentar conexión</button>
      </main>
    );
  if (auth.logoutPending)
    return (
      <main className="min-h-screen bg-gray-950 text-gray-200 p-6">
        <p role="alert">{auth.message}</p>
        <button
          onClick={() =>
            void logoutBrowser(auth.logoutAllPending).catch(() => {})
          }
        >
          Reintentar cierre de sesión
        </button>
      </main>
    );
  if (auth.status === "unavailable" && !auth.session)
    return (
      <main className="min-h-screen bg-gray-950 text-gray-200 p-6">
        <p role="alert">{auth.message}</p>
        <button onClick={() => void initializeBrowserAuth()}>
          Reintentar conexión
        </button>
      </main>
    );
  if (auth.mode === "bearer" && auth.config?.local_setup_available === true)
    return <BrowserAccess setupAvailable />;
  if (auth.mode === "local_password" && !auth.session) return <BrowserAccess />;
  if (!fleetReady) return <main role="status">Comprobando nodos…</main>;
  return (
    <>
      {auth.status === "unavailable" && (
        <div role="alert" className="bg-amber-950 text-amber-200 p-3">
          {auth.message}
        </div>
      )}
      <Dashboard
        key={`${auth.session?.session_id ?? "traditional"}:${nodeEpoch}`}
      />
    </>
  );
}
