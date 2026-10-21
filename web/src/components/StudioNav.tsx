// Studio navigation (spec 019, P1): destinations grouped by objective plus a
// context bar stating where the operator is acting and as whom. Shortcuts
// stay bound to each destination (not to its position in the strip), so the
// grouping does not renumber any existing Alt+N shortcut.
import type { ReactNode, KeyboardEvent } from "react";
import type { BrowserAuthState } from "../auth";

export type StudioGroup = "work" | "operation" | "library" | "administration";

export const GROUP_LABELS: Record<StudioGroup, string> = {
  work: "Work",
  operation: "Operation",
  library: "Library",
  administration: "Administration",
};

export const GROUP_HINTS: Record<StudioGroup, string> = {
  work: "Running agents, questions and tasks on the selected node",
  operation: "Workflows and scheduled runs",
  library: "Reusable definitions: profiles, memory and plugins",
  administration: "Server settings",
};

export const GROUP_ORDER: StudioGroup[] = ["work", "operation", "library", "administration"];

export interface StudioDestination<K extends string> {
  key: K;
  label: string;
  icon: ReactNode;
  group: StudioGroup;
}

/** Shortcut label for the destination at `index` among visible destinations. */
export function shortcutLabel(index: number): string | null {
  if (index < 9) return `Alt+${index + 1}`;
  if (index === 9) return "Alt+0";
  if (index === 10) return "Alt+Shift+1";
  if (index === 11) return "Alt+Shift+2";
  return null;
}

/** Stable DOM ids tying each tab to the single panel that shows the active destination. */
export const studioTabId = (prefix: string, key: string) => `${prefix}-tab-${key}`;
export const studioPanelId = (prefix: string) => `${prefix}-panel`;

export function StudioNav<K extends string>({
  destinations,
  current,
  locked,
  badges,
  onSelect,
  idPrefix = "studio",
}: {
  /** Prefix for tab/panel ids; must be unique per rendered strip. */
  idPrefix?: string;
  /** Visible destinations in shortcut order. */
  destinations: StudioDestination<K>[];
  current: K;
  locked: boolean;
  badges?: Partial<Record<K, number>>;
  onSelect: (key: K) => void;
}) {
  const shortcutIndex = new Map(destinations.map((d, i) => [d.key, i]));
  const groups = GROUP_ORDER.map((g) => ({
    group: g,
    items: destinations.filter((d) => d.group === g),
  })).filter((g) => g.items.length > 0);

  // Tabs with manual activation: only the selected tab is in the Tab order,
  // arrow keys/Home/End move focus across the whole strip and selection still
  // requires Enter/Space or click (both go through onSelect, which honours
  // the save lock).
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const tabs = Array.from(
      e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
    );
    const at = tabs.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0) return;
    const next =
      e.key === "ArrowRight"
        ? (at + 1) % tabs.length
        : e.key === "ArrowLeft"
          ? (at - 1 + tabs.length) % tabs.length
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? tabs.length - 1
              : -1;
    if (next < 0) return;
    e.preventDefault();
    tabs[next].focus();
  };

  return (
    <nav
      className="flex flex-wrap items-end gap-x-4 gap-y-2 py-2"
      role="tablist"
      aria-label="Studio areas"
      onKeyDown={onKeyDown}
    >
      {groups.map(({ group, items }) => (
        <div key={group} role="presentation" className="flex min-w-0 max-w-full flex-col gap-1">
          <span
            id={`studio-group-${group}`}
            className="px-1 text-[10px] font-semibold uppercase tracking-wide text-gray-500"
            title={GROUP_HINTS[group]}
          >
            {GROUP_LABELS[group]}
          </span>
          <div role="presentation" className="flex flex-wrap gap-1">
            {items.map((t) => {
              const selected = current === t.key;
              const shortcut = shortcutLabel(shortcutIndex.get(t.key) ?? -1);
              const badge = badges?.[t.key];
              return (
                <button
                  key={t.key}
                  type="button"
                  role="tab"
                  id={studioTabId(idPrefix, t.key)}
                  tabIndex={selected ? 0 : -1}
                  aria-selected={selected}
                  aria-controls={selected ? studioPanelId(idPrefix) : undefined}
                  aria-disabled={locked && !selected}
                  aria-describedby={`studio-group-${group}`}
                  aria-keyshortcuts={shortcut ?? undefined}
                  onClick={() => onSelect(t.key)}
                  className={`px-3 py-2 rounded-lg text-sm font-medium transition-all duration-200 flex items-center gap-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                    selected
                      ? "bg-gradient-to-r from-emerald-600 to-emerald-500 text-white shadow-lg shadow-emerald-500/20"
                      : locked
                        ? "text-gray-600 cursor-not-allowed"
                        : "text-gray-400 hover:text-white hover:bg-gray-800/50"
                  }`}
                  title={
                    locked && !selected
                      ? "Finish or cancel the in-flight save first"
                      : (shortcut ?? undefined)
                  }
                >
                  {t.icon}
                  {t.label}
                  {badge !== undefined && badge > 0 && (
                    <span
                      className={`px-1.5 py-0.5 text-xs rounded-full ${selected ? "bg-white/20" : "bg-gray-700"}`}
                    >
                      {badge}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

/** Who the browser acts as, from the real browser-auth state only. */
export function readerLabel(auth: Pick<BrowserAuthState, "mode" | "status" | "session">): string {
  if (auth.mode === "disabled") return "No sign-in (authentication disabled on this server)";
  if (auth.mode === "local_password") {
    if (auth.status === "authenticated" || auth.status === "renewing") {
      return auth.session?.username ? `Signed in as ${auth.session.username}` : "Signed in";
    }
    return "Not signed in";
  }
  if (auth.mode === "bearer") {
    return auth.status === "authenticated" ? "Access token" : "Access token not accepted";
  }
  return "Checking access…";
}

export function StudioContextBar({
  node,
  nodeOnline,
  auth,
}: {
  /** Active fleet node, or null for the server this page was opened from. */
  node: string | null;
  /** Fleet registry reachability for the node; undefined when unknown. */
  nodeOnline?: boolean;
  auth: Pick<BrowserAuthState, "mode" | "status" | "session">;
}) {
  return (
    <section
      aria-label="Current context"
      className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-gray-400"
    >
      <p>
        <span className="text-gray-500">Node: </span>
        <span className="text-gray-200">{node ?? "This server"}</span>
        {node && nodeOnline === false && (
          <span className="ml-1 text-amber-300">(registry reports offline)</span>
        )}
      </p>
      <p>
        <span className="text-gray-500">Acting as: </span>
        <span className="text-gray-200">{readerLabel(auth)}</span>
      </p>
      <p>
        <span className="text-gray-500">Permissions: </span>
        <span className="text-gray-200">
          not summarised by the server; each area shows what you can do when opened
        </span>
      </p>
    </section>
  );
}
