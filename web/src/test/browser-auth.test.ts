import { afterEach, describe, expect, it, vi } from "vitest";
import { api, terminalSocketUrl } from "../api";
import { browserFetch, consumeBrowserLink, setBrowserBearer } from "../auth";

const token = "signed.operator.token";
afterEach(() => {
  sessionStorage.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
describe("authenticated browser transports", () => {
  it("consumes a private access fragment without leaving it in history", () => {
    history.replaceState(
      null,
      "",
      "/?view=home#cao_token=signed.operator.token",
    );
    consumeBrowserLink();
    expect(location.hash).toBe("");
    expect(location.search).toBe("?view=home");
    expect(sessionStorage.getItem("cao.browser.bearer")).toBe(token);
    expect(localStorage.getItem("cao.browser.bearer")).toBeNull();
    history.replaceState(null, "", "/");
  });
  it("adds bearer without losing SSE headers", async () => {
    setBrowserBearer(token);
    const fetch = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await browserFetch("/workflows/runs/run/events", {
      headers: { Accept: "text/event-stream" },
    });
    const headers = new Headers(fetch.mock.calls[0][1].headers);
    expect(headers.get("Accept")).toBe("text/event-stream");
    expect(headers.get("Authorization")).toBe(`Bearer ${token}`);
  });
  it("rejects cross-origin transport before sending credentials", async () => {
    setBrowserBearer(token);
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(
      browserFetch("https://other.invalid/sessions"),
    ).rejects.toThrow("mismo origen");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects malformed tokens", () => {
    expect(() => setBrowserBearer("bad\nheader")).toThrow(
      "Formato de token inválido",
    );
    expect(sessionStorage.getItem("cao.browser.bearer")).toBeNull();
  });
  it("does not clear a replacement credential after an older request fails", async () => {
    setBrowserBearer(token);
    let complete!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            complete = resolve;
          }),
      ),
    );
    const pending = browserFetch("/sessions");
    setBrowserBearer("fresh.operator.token");
    complete(new Response("{}", { status: 401 }));
    await pending;
    expect(sessionStorage.getItem("cao.browser.bearer")).toBe(
      "fresh.operator.token",
    );
  });
  it("uses tab bearer for protected REST requests", async () => {
    sessionStorage.setItem("cao.browser.bearer", token);
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await api.listSessions();
    const options = fetch.mock.calls[0][1];
    expect(new Headers(options.headers).get("Authorization")).toBe(
      `Bearer ${token}`,
    );
    expect(options.redirect).toBe("error");
  });
  it("never puts reusable bearer into websocket URLs", () => {
    sessionStorage.setItem("cao.browser.bearer", token);
    expect(terminalSocketUrl("term-1")).not.toContain(token);
    expect(new URL(terminalSocketUrl("term-1")).searchParams.has("token")).toBe(
      false,
    );
  });
  it("clears rejected bearer and reports required authentication", async () => {
    sessionStorage.setItem("cao.browser.bearer", token);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 401 })),
    );
    const listener = vi.fn();
    window.addEventListener("cao-auth-required", listener);
    try {
      await expect(api.listSessions()).rejects.toThrow("401");
      expect(sessionStorage.getItem("cao.browser.bearer")).toBeNull();
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("cao-auth-required", listener);
    }
  });
});

describe("bounded authentication requests", () => {
  it.each(["fetch", "body"])(
    "bounds a stalled %s to ten seconds and exposes unavailable",
    async (stage) => {
      vi.useFakeTimers();
      vi.resetModules();
      const auth = await import("../auth");
      let signal: AbortSignal | undefined;
      vi.stubGlobal(
        "fetch",
        vi.fn((_url, options) => {
          signal = options.signal;
          if (stage === "fetch") return new Promise(() => {});
          return Promise.resolve(
            new Response(new ReadableStream({ start() {} })),
          );
        }),
      );
      const pending = auth.initializeBrowserAuth();
      await vi.advanceTimersByTimeAsync(10000);
      expect(signal?.aborted).toBe(true);
      expect(auth.authSnapshot().status).toBe("unavailable");
      await pending;
      vi.useRealTimers();
    },
  );
});

it("keeps the local session on renewal timeout and cancels a stalled response body", async () => {
  vi.useFakeTimers();
  vi.resetModules();
  const auth = await import("../auth");
  const session = {
    session_id: "retained",
    username: "operator",
    remembered: false,
    access_expires_at: 1000,
    idle_expires_at: 2000,
    absolute_expires_at: 3000,
    server_time: 0,
    session_revision: 1,
  };
  const cancelledBody = vi.fn();
  let renewalSignal: AbortSignal | undefined;
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ mode: "local_password" })),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify(session)))
      .mockImplementationOnce((_url, options) => {
        renewalSignal = options.signal;
        return new Promise<Response>((resolve) =>
          setTimeout(
            () =>
              resolve(
                new Response(
                  new ReadableStream({ start() {}, cancel: cancelledBody }),
                ),
              ),
            9000,
          ),
        );
      }),
  );
  await auth.initializeBrowserAuth();
  const renewal = auth.renewBrowserSession();
  const rejection = expect(renewal).rejects.toThrow("tiempo de espera agotado");
  await vi.advanceTimersByTimeAsync(10000);
  await rejection;
  expect(renewalSignal?.aborted).toBe(true);
  expect(cancelledBody).toHaveBeenCalledOnce();
  expect(auth.authSnapshot()).toMatchObject({
    status: "unavailable",
    session: { session_id: "retained" },
  });
});

it("confirms logout when a 204 response exposes an empty readable body", async () => {
  const auth = await import("../auth");
  const session = {
    session_id: "logout-stream",
    username: "operator",
    remembered: true,
    access_expires_at: 4600,
    idle_expires_at: 9000,
    absolute_expires_at: 10000,
    server_time: 1000,
    session_revision: 1,
  };
  const logout = new Response(null, { status: 204 });
  // Chromium exposes an empty stream for this real no-content HTTP response.
  Object.defineProperty(logout, "body", {
    value: new ReadableStream({
      start(controller) {
        controller.close();
      },
    }),
  });
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ mode: "local_password" })),
    )
    .mockResolvedValueOnce(new Response(JSON.stringify(session)))
    .mockResolvedValueOnce(logout)
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { code: "session_revoked" } }), {
        status: 401,
      }),
    );
  vi.stubGlobal("fetch", fetch);
  await auth.initializeBrowserAuth();
  await expect(auth.logoutBrowser()).resolves.toBeUndefined();
  expect(fetch.mock.calls.map((call) => call[0])).toEqual([
    "/auth/config",
    "/auth/session",
    "/auth/logout",
    "/auth/session",
  ]);
  expect(auth.authSnapshot().status).toBe("anonymous");
  expect(auth.authSnapshot().logoutPending).toBe(false);
});
