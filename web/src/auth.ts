const TOKEN_KEY = "cao.browser.bearer";
const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");
export type BrowserSession = {
  session_id: string;
  username: string;
  remembered: boolean;
  access_expires_at: number;
  idle_expires_at: number;
  absolute_expires_at: number;
  server_time: number;
  session_revision: number;
};
export type BrowserAuthState = {
  mode: "local_password" | "bearer" | "disabled" | null;
  status:
    | "initializing"
    | "anonymous"
    | "authenticated"
    | "renewing"
    | "unavailable"
    | "expired"
    | "revoked";
  session: BrowserSession | null;
  message?: string;
  logoutPending?: boolean;
  logoutAllPending?: boolean;
  config?: Record<string, unknown>;
};
let state: BrowserAuthState = {
  mode: null,
  status: "initializing",
  session: null,
};
let generation = 0;
let authority = new AbortController();
let renewedAt = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let renewal: Promise<void> | null = null;
const listeners = new Set<() => void>();
let channel: BroadcastChannel | undefined;
export const authSnapshot = () => state;
export const browserAuthSignal = () => authority.signal;
export function subscribeBrowserAuth(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
function update(patch: Partial<BrowserAuthState>) {
  state = { ...state, ...patch };
  listeners.forEach((listener) => listener());
}
function stopAuthority() {
  generation++;
  authority.abort(new Error("La sesión ha cambiado"));
  authority = new AbortController();
  clearTimeout(timer);
}
function accept(session: BrowserSession) {
  if (state.session && state.session.session_id !== session.session_id) {
    stopAuthority();
    renewal = null;
  }
  renewedAt = performance.now();
  update({
    session,
    status: "authenticated",
    message: undefined,
    logoutPending: false,
  });
  clearTimeout(timer);
  const delay = Math.max(
    1000,
    (session.access_expires_at - session.server_time - 30) * 1000,
  );
  timer = setTimeout(() => {
    void renewBrowserSession().catch(() => {});
  }, delay);
}
function unavailable() {
  clearTimeout(timer);
  update({
    status: "unavailable",
    message: "Servidor no disponible. Tu sesión no se ha revocado.",
  });
  if (state.session && !state.logoutPending)
    timer = setTimeout(() => {
      void renewBrowserSession().catch(() => {});
    }, 15000);
}
export class BrowserAuthError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public retryAfter?: string | null,
  ) {
    super(message);
  }
}
async function codeOf(response: Response) {
  try {
    return (await response.clone().json()).detail?.code as string | undefined;
  } catch {
    return undefined;
  }
}
async function authRequest(
  path: string,
  method = "GET",
  body?: unknown,
  bearer?: string,
): Promise<Response> {
  const headers = new Headers();
  if (method !== "GET") headers.set("X-CAO-Browser", "1");
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
  const expected = generation;
  const controller = new AbortController();
  const signal = AbortSignal.any([authority.signal, controller.signal]);
  const deadline = setTimeout(
    () =>
      controller.abort(
        new Error("Autenticación no disponible: tiempo de espera agotado"),
      ),
    10000,
  );
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let onAbort!: () => void;
  const cancelled = new Promise<never>((_resolve, reject) => {
    onAbort = () => {
      void reader?.cancel().catch(() => {});
      reject(signal.reason ?? new Error("Solicitud cancelada"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
  });
  let response: Response;
  try {
    response = await Promise.race([
      (async () => {
        const result = await fetch(`${BASE}/auth/${path}`, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          credentials: "same-origin",
          redirect: "error",
          cache: "no-store",
          signal,
        });
        if (signal.aborted) throw signal.reason;
        // Buffer the body within the same deadline, so later json() cannot stall renewal.
        if (!result.body) return result;
        reader = result.body.getReader();
        const chunks: Uint8Array[] = [];
        let length = 0;
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          chunks.push(chunk.value);
          length += chunk.value.byteLength;
        }
        const bytes = new Uint8Array(length);
        let offset = 0;
        for (const chunk of chunks) {
          bytes.set(chunk, offset);
          offset += chunk.byteLength;
        }
        return new Response(length === 0 ? null : bytes, {
          status: result.status,
          statusText: result.statusText,
          headers: result.headers,
        });
      })(),
      cancelled,
    ]);
  } catch (error) {
    if (expected === generation && !state.logoutPending) unavailable();
    throw error;
  } finally {
    clearTimeout(deadline);
    signal.removeEventListener("abort", onAbort);
    reader?.releaseLock();
  }
  if (!response.ok) {
    let detail: { code?: string; message?: string } = {};
    try {
      detail = (await response.json()).detail ?? {};
    } catch {
      /* fixed public error */
    }
    const messages: Record<string, string> = {
      credentials_rejected: "Usuario o contraseña incorrectos.",
      setup_publication_uncertain:
        "No se ha podido confirmar la creación de la cuenta. Comprueba el acceso e inicia sesión con la contraseña que acabas de introducir.",
      session_required: "Inicia sesión para continuar.",
      session_expired: "La sesión ha caducado. Vuelve a iniciar sesión.",
      session_revoked: "La sesión se ha revocado. Vuelve a iniciar sesión.",
      access_renewal_required: "Es necesario renovar el acceso.",
      login_throttled:
        "Demasiados intentos. Espera antes de volver a intentarlo.",
      invalid_auth_request:
        "Revisa los datos introducidos e inténtalo de nuevo.",
      auth_body_too_large:
        "Los datos introducidos superan el tamaño permitido.",
      browser_origin_denied:
        "Abre esta instalación desde su dirección de acceso autorizada.",
      local_login_disabled: "El inicio de sesión local no está disponible.",
    };
    const code = detail.code ?? "auth_unavailable";
    throw new BrowserAuthError(
      code,
      messages[code] ??
        "Autenticación no disponible. Vuelve a intentarlo cuando el servidor esté disponible.",
      response.status,
      response.headers.get("Retry-After"),
    );
  }
  return response;
}
function failed(error: unknown, expected: number) {
  if (expected !== generation) return;
  if (
    error instanceof BrowserAuthError &&
    ["session_required", "session_expired", "session_revoked"].includes(
      error.code,
    )
  ) {
    stopAuthority();
    update({
      session: null,
      status:
        error.code === "session_required"
          ? "anonymous"
          : error.code === "session_revoked"
            ? "revoked"
            : "expired",
      message: error.message,
    });
  } else unavailable();
}
async function locked<T>(operation: () => Promise<T>): Promise<T> {
  if (navigator.locks)
    return navigator.locks.request("cao-browser-auth", operation);
  return operation();
}
function advise() {
  try {
    channel?.postMessage({ type: "session-changed" });
  } catch {
    /* optional advisory */
  }
}
function coordinate() {
  if (channel || typeof BroadcastChannel === "undefined") return;
  try {
    channel = new BroadcastChannel(
      `cao-browser-auth:${location.origin}${BASE}`,
    );
    channel.onmessage = () => {
      if (state.mode === "local_password" && !state.logoutPending)
        void refreshBrowserSession();
    };
  } catch {
    /* Advisory coordination is optional. Server remains authoritative. */
  }
  window.addEventListener("focus", () => {
    if (state.mode === "local_password" && !state.logoutPending)
      void refreshBrowserSession();
  });
}
export async function initializeBrowserAuth() {
  stopAuthority();
  renewal = null;
  const expected = generation;
  update({ status: "initializing", session: null, logoutPending: false });
  try {
    const config = await (await authRequest("config")).json();
    if (expected !== generation) return;
    if (!["local_password", "bearer", "disabled"].includes(config.mode))
      throw new Error("La configuración de autenticación no es válida");
    update({ mode: config.mode, config });
    if (config.mode === "local_password") {
      try {
        sessionStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(TOKEN_KEY);
      } catch {
        /* Cookie auth does not require Web Storage. */
      }
      consumeBrowserLink();
      coordinate();
      await refreshBrowserSession();
    } else {
      consumeBrowserLink();
      update({ status: "authenticated" });
    }
  } catch (error) {
    failed(error, expected);
  }
}
export async function refreshBrowserSession() {
  const expected = generation;
  try {
    const session = await (await authRequest("session")).json();
    if (expected === generation) accept(session);
  } catch (error) {
    failed(error, expected);
  }
}
export async function loginBrowser(
  username: string,
  password: string,
  remember: boolean,
) {
  if (state.logoutPending)
    throw new Error("Reintenta primero el cierre de sesión pendiente");
  return locked(async () => {
    stopAuthority();
    const expected = generation;
    await authRequest("login", "POST", { username, password, remember });
    try {
      const session = await (await authRequest("session")).json();
      if (expected === generation) {
        accept(session);
        advise();
      }
    } catch (error) {
      failed(error, expected);
      if (
        error instanceof BrowserAuthError &&
        error.code === "session_required"
      )
        throw new Error(
          "Las cookies están bloqueadas. Permite las cookies de este sitio para iniciar sesión.",
        );
      throw error;
    }
  });
}
/** First-account setup consumes the existing operator access, then switches to cookies. */
export async function setupBrowser(
  username: string,
  password: string,
  remember: boolean,
) {
  if (state.mode !== "bearer" || state.config?.local_setup_available !== true)
    throw new BrowserAuthError(
      "setup_unavailable",
      "La creación de cuenta no está disponible",
      409,
    );
  const bearer = browserBearer();
  if (!bearer)
    throw new BrowserAuthError(
      "operator_access_required",
      "Se requiere un acceso autorizado",
      401,
    );
  return locked(async () => {
    await authRequest(
      "setup",
      "POST",
      { username, password, remember },
      bearer,
    );
    await initializeBrowserAuth();
    if (state.mode !== "local_password" || !state.session) {
      throw new BrowserAuthError(
        "setup_session_required",
        "Es necesario confirmar la sesión de la cuenta creada",
        503,
      );
    }
    advise();
  });
}
export async function renewBrowserSession(): Promise<void> {
  if (state.mode !== "local_password") return;
  if (state.logoutPending || !state.session)
    throw new Error("Inicia sesión para continuar");
  if (renewal) return renewal;
  const expected = generation;
  update({ status: "renewing" });
  const pending = (async () => {
    try {
      const session = await (await authRequest("renew", "POST")).json();
      if (expected === generation) accept(session);
    } catch (error) {
      failed(error, expected);
      throw error;
    }
  })();
  renewal = pending;
  try {
    await pending;
  } finally {
    if (renewal === pending) renewal = null;
  }
}
export async function ensureBrowserAccess() {
  if (state.mode !== "local_password") return;
  if (!state.session || state.logoutPending)
    throw new Error("Inicia sesión para continuar");
  const expected = generation;
  if (
    state.status === "unavailable" ||
    state.session.access_expires_at -
      state.session.server_time -
      (performance.now() - renewedAt) / 1000 <=
      30
  )
    await renewBrowserSession();
  if (expected !== generation || !state.session || state.logoutPending)
    throw new Error("La sesión ha cambiado");
}
export async function logoutBrowser(all = false) {
  return locked(async () => {
    const retrying = state.logoutPending;
    const targetId = state.session?.session_id;
    stopAuthority();
    renewal = null;
    const expected = generation;
    update({
      status: "unavailable",
      logoutPending: true,
      logoutAllPending: all,
      message:
        "El cierre de sesión está pendiente de confirmación. Reinténtalo cuando el servidor esté disponible.",
    });
    const confirm = async (): Promise<BrowserSession | null> => {
      try {
        return await (await authRequest("session")).json();
      } catch (error) {
        if (
          error instanceof BrowserAuthError &&
          ["session_required", "session_revoked", "session_expired"].includes(
            error.code,
          )
        )
          return null;
        throw error;
      }
    };
    const finish = (session: BrowserSession | null) => {
      if (expected !== generation) return;
      if (session) accept(session);
      else
        update({
          session: null,
          status: "anonymous",
          logoutPending: false,
          message: undefined,
        });
      advise();
    };
    try {
      if (retrying) {
        const current = await confirm();
        if (!current || current.session_id !== targetId) {
          finish(current);
          return;
        }
      }
      await authRequest(all ? "logout-all" : "logout", "POST");
      finish(await confirm());
    } catch (error) {
      if (expected === generation)
        update({
          message:
            "El cierre de sesión está pendiente de confirmación. Reinténtalo cuando el servidor esté disponible.",
        });
      throw error;
    }
  });
}
export async function changeBrowserPassword(
  current_password: string,
  new_password: string,
) {
  await ensureBrowserAccess();
  const expected = generation;
  await authRequest("password", "POST", { current_password, new_password });
  if (expected === generation) {
    stopAuthority();
    update({
      session: null,
      status: "revoked",
      message: "Contraseña cambiada. Vuelve a iniciar sesión.",
    });
    advise();
  }
}
export function browserBearer(): string | null {
  if (state.mode === "local_password") return null;
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token && /^[A-Za-z0-9._~-]+$/.test(token)) return token;
  sessionStorage.removeItem(TOKEN_KEY);
  return null;
}
export function setBrowserBearer(token: string) {
  if (!/^[A-Za-z0-9._~-]+$/.test(token))
    throw new Error("Formato de token inválido");
  sessionStorage.setItem(TOKEN_KEY, token);
}
export function consumeBrowserLink() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const token = fragment.get("cao_token");
  if (token === null) return;
  fragment.delete("cao_token");
  const remaining = fragment.toString();
  history.replaceState(
    history.state,
    "",
    location.pathname + location.search + (remaining ? `#${remaining}` : ""),
  );
  if (state.mode === "local_password") return;
  try {
    setBrowserBearer(token);
  } catch {
    sessionStorage.removeItem(TOKEN_KEY);
  }
}
/** Shared REST/SSE boundary; only safe reads can retry after renewal. */
export async function browserFetch(
  url: string,
  options?: RequestInit,
): Promise<Response> {
  if (new URL(url, location.href).origin !== location.origin)
    throw new Error("Las solicitudes de CAO deben usar el mismo origen");
  const local = state.mode === "local_password";
  const method = (options?.method ?? "GET").toUpperCase();
  const safe = ["GET", "HEAD"].includes(method);
  if (local) await ensureBrowserAccess();
  const expected = generation;
  const token = browserBearer();
  const headers = new Headers(options?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (local && !safe) headers.set("X-CAO-Browser", "1");
  const signal = local
    ? AbortSignal.any([
        authority.signal,
        ...(options?.signal ? [options.signal] : []),
      ])
    : options?.signal;
  const request = {
    ...options,
    headers: local || token ? headers : options?.headers,
    signal,
    credentials: "same-origin" as const,
    redirect: "error" as const,
  };
  let response: Response;
  try {
    response = await fetch(url, request);
  } catch (error) {
    if (local && expected === generation) {
      if (!signal?.aborted) unavailable();
      if (!safe)
        throw new Error(
          "El resultado de la operación es incierto. Comprueba su estado antes de volver a intentarlo.",
        );
    }
    throw error;
  }
  if (expected !== generation) {
    if (local) throw new Error("La sesión ha cambiado");
    return response;
  }
  if (
    local &&
    response.status === 503 &&
    ["auth_unavailable", "clock_untrusted"].includes(
      (await codeOf(response)) ?? "",
    )
  )
    unavailable();
  if (local && response.status === 401) {
    const code = await codeOf(response);
    if (code === "access_renewal_required" && safe) {
      await renewBrowserSession();
      if (expected !== generation) throw new Error("La sesión ha cambiado");
      response = await fetch(url, request);
      if (expected !== generation) throw new Error("La sesión ha cambiado");
      if (expected === generation && response.status === 401)
        failed(
          new BrowserAuthError(
            (await codeOf(response)) ?? "session_required",
            "La sesión ha caducado o se ha revocado",
            401,
          ),
          expected,
        );
    } else if (code !== "access_renewal_required")
      failed(
        new BrowserAuthError(
          code ?? "session_required",
          "La sesión ha caducado o se ha revocado",
          401,
        ),
        expected,
      );
  } else if (!local && response.status === 401 && browserBearer() === token) {
    sessionStorage.removeItem(TOKEN_KEY);
    window.dispatchEvent(new Event("cao-auth-required"));
  }
  return response;
}
