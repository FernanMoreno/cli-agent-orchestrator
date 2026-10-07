// Event-stream view (ui://cao/event-stream).
//
// Hydrates the governance ticker via `cao_fetch_history`, then subscribes to the
// live SSE feed at the descriptor returned by `subscribe_events`. Re-mounting
// re-fetches history (Req: re-mount idempotence). SSE gaps are backfilled by the
// cursor replay, so events retained by the server are recovered after a drop.

import React, { useEffect, useRef, useState } from "react";
import { EventStream } from "../shared/EventStream";
import { HeaderBar } from "../shared/HeaderBar";
import { McpApp } from "../shared/mcpApp";
import type { CaoEvent } from "../shared/types";

const HISTORY_LIMIT = 500;

export interface EventStreamViewProps {
  app?: McpApp;
  initialEvents?: CaoEvent[];
  /** Base URL for the Backplane SSE endpoint (loopback by default). */
  backplaneBaseUrl?: string;
  /** Injectable EventSource for tests (defaults to the global). */
  eventSourceFactory?: (url: string) => EventSourceLike;
}

/** Minimal EventSource surface we depend on (keeps tests simple). */
export interface EventSourceLike {
  addEventListener(
    type: "message" | "cursor_expired",
    listener: (ev: { data: string }) => void,
  ): void;
  close(): void;
  onerror?: (() => void) | null;
}

export function EventStreamView({
  app,
  initialEvents,
  backplaneBaseUrl = "http://127.0.0.1:9889",
  eventSourceFactory,
}: EventStreamViewProps): JSX.Element {
  const [unavailable, setUnavailable] = useState(false);
  const [resyncing, setResyncing] = useState(false);
  const [events, setEvents] = useState<CaoEvent[]>(initialEvents ?? []);
  const seen = useRef<Set<string>>(
    new Set((initialEvents ?? []).map((e) => e.id)),
  );

  function ingest(incoming: CaoEvent[]): void {
    const fresh = incoming.filter((e) => e && e.id && !seen.current.has(e.id));
    if (fresh.length === 0) return;
    for (const e of fresh) seen.current.add(e.id);
    setEvents((prev) => [...prev, ...fresh].slice(-HISTORY_LIMIT));
  }

  useEffect(() => {
    if (!app) return;
    let source: EventSourceLike | undefined;
    let cancelled = false;

    let cursor: string | null = null;
    let resyncRequired = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    const retry = () => {
      if (cancelled || reconnectTimer) return;
      setUnavailable(true);
      reconnectTimer = setTimeout(() => {
        reconnectTimer = undefined;
        void subscribe();
      }, 1000);
    };
    const subscribe = async () => {
      if (cancelled) return;
      try {
        if (resyncRequired) {
          const snapshot = await app.fetchHistorySnapshot(HISTORY_LIMIT);
          if (cancelled) return;
          ingest(snapshot.events);
          cursor = snapshot.cursor;
          resyncRequired = false;
        }
        // Every attempt obtains a new single-use ticket and replays after the
        // last accepted event, including events emitted during the history gap.
        let desc = await app.callServerTool("subscribe_events", {
          last_event_id: cursor,
        });
        if (cancelled) return;
        if (desc?.resync_required) {
          const snapshot = await app.fetchHistorySnapshot(HISTORY_LIMIT);
          if (cancelled) return;
          ingest(snapshot.events);
          cursor = snapshot.cursor;
          desc = await app.callServerTool("subscribe_events", {
            last_event_id: cursor,
          });
          if (desc?.resync_required)
            throw new Error("Event cursor expired during resync");
        }
        if (cancelled) return;
        const sseUrl = new URL(
          desc?.url ?? desc?.sse_url ?? "/events",
          `${backplaneBaseUrl.replace(/\/$/, "")}/`,
        ).href;
        const factory =
          eventSourceFactory ??
          ((url: string) => new EventSource(url) as unknown as EventSourceLike);
        const current = factory(sseUrl);
        source = current;
        setUnavailable(false);
        setResyncing(false);
        current.addEventListener("message", (ev) => {
          if (cancelled || source !== current) return;
          try {
            const parsed = JSON.parse(ev.data) as CaoEvent;
            if (!parsed || typeof parsed.id !== "string" || !parsed.id) return;
            if (!seen.current.has(parsed.id)) {
              ingest([parsed]);
              cursor = parsed.id;
            }
            setUnavailable(false);
          } catch {
            /* Malformed frames cannot advance the confirmed cursor. */
          }
        });
        current.addEventListener("cursor_expired", (ev) => {
          if (cancelled || source !== current) return;
          try {
            const frame = JSON.parse(ev.data);
            if (
              frame?.code !== "event_cursor_expired" ||
              frame?.resync_required !== true
            )
              return;
          } catch {
            return;
          }
          current.close();
          source = undefined;
          resyncRequired = true;
          setResyncing(true);
          retry();
        });
        current.onerror = () => {
          if (cancelled || source !== current) return;
          // Native EventSource retries would reuse a consumed ticket.
          current.close();
          source = undefined;
          retry();
        };
      } catch {
        retry();
      }
    };
    void app
      .connect()
      .then(async () => {
        const history = await app.fetchHistorySnapshot(HISTORY_LIMIT);
        if (cancelled) return;
        ingest(history.events);
        cursor = history.cursor;
        await subscribe();
      })
      .catch(() => {
        if (!cancelled) setUnavailable(true);
      });

    return () => {
      cancelled = true;
      clearTimeout(reconnectTimer);
      if (source) source.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [app]);

  return (
    <div className="cao-root">
      <HeaderBar title="Governance Stream" />
      {unavailable && (
        <p role="status">
          {resyncing
            ? "Live history expired. Reloading retained events."
            : "Live events unavailable. Reconnecting when possible."}
        </p>
      )}
      <EventStream events={events} emptyLabel="No fleet events yet" />
    </div>
  );
}
