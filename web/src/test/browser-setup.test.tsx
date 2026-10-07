import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App";
import { BrowserSetup } from "../components/BrowserSetup";
import { initializeBrowserAuth } from "../auth";
vi.mock("../components/AgentPanel", () => ({ AgentPanel: () => null }));
vi.mock("../components/ProfilesPanel", () => ({ ProfilesPanel: () => null }));
vi.mock("../components/DashboardHome", () => ({
  DashboardHome: () => <div>Protected dashboard</div>,
}));
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
const setupConfig = { mode: "bearer", local_setup_available: true };
const session = {
  session_id: "setup-session",
  username: "felni",
  remembered: false,
  access_expires_at: 4600,
  idle_expires_at: 9000,
  absolute_expires_at: 10000,
  server_time: 1000,
  session_revision: 1,
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  history.replaceState({}, "", "/");
});
it("gates the main frontend behind setup and consumes the authorized access link", async () => {
  history.replaceState({}, "", "/#cao_token=owner.signed.token");
  const fetch = vi.fn().mockResolvedValue(json(setupConfig));
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  await screen.findByLabelText("Usuario");
  fireEvent.click(screen.getByRole("tab", { name: "Crear cuenta" }));
  expect(
    screen.getByRole("heading", { name: "Crear cuenta" }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Usuario")).toHaveValue("felni");
  expect(screen.getByLabelText("Contraseña")).toHaveAttribute(
    "autocomplete",
    "new-password",
  );
  expect(screen.getByLabelText("Contraseña")).toHaveAttribute(
    "minlength",
    "10",
  );
  expect(screen.getByLabelText("Recordar este navegador")).not.toBeChecked();
  expect(screen.queryByText("Protected dashboard")).toBeNull();
  expect(fetch.mock.calls.map((call) => call[0])).toEqual(["/auth/config"]);
  expect(location.hash).toBe("");
});
it("does not send mismatched passwords or mount the protected frontend", async () => {
  const fetch = vi.fn().mockResolvedValue(json(setupConfig));
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  await screen.findByLabelText("Usuario");
  fireEvent.click(screen.getByRole("tab", { name: "Crear cuenta" }));
  await screen.findByLabelText("Usuario");
  fireEvent.change(screen.getByLabelText("Contraseña"), {
    target: { value: "a long secret password" },
  });
  fireEvent.change(screen.getByLabelText("Confirmar contraseña"), {
    target: { value: "different secret password" },
  });
  fireEvent.submit(screen.getByLabelText("Usuario").closest("form")!);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Las contraseñas no coinciden",
  );
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("Protected dashboard")).toBeNull();
});
it("sets up through the bearer, verifies the cookie and opens the main frontend", async () => {
  history.replaceState({}, "", "/#cao_token=owner.signed.token");
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(json(setupConfig))
    .mockResolvedValueOnce(json(session))
    .mockResolvedValueOnce(json({ mode: "local_password" }))
    .mockResolvedValueOnce(json(session))
    .mockImplementation(async (url: string) =>
      url === "/api/fleet"
        ? json({ detail: "Not Found" }, 404)
        : json(url === "/sessions" ? [] : { enabled: false }),
    );
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  await screen.findByLabelText("Usuario");
  fireEvent.click(screen.getByRole("tab", { name: "Crear cuenta" }));
  await screen.findByLabelText("Usuario");
  fireEvent.change(screen.getByLabelText("Contraseña"), {
    target: { value: "TenChars1!" },
  });
  fireEvent.change(screen.getByLabelText("Confirmar contraseña"), {
    target: { value: "TenChars1!" },
  });
  fireEvent.submit(screen.getByLabelText("Usuario").closest("form")!);
  expect(await screen.findByText("Protected dashboard")).toBeInTheDocument();
  const call = fetch.mock.calls.find((call) => call[0] === "/auth/setup")!;
  expect(call[1].method).toBe("POST");
  expect(new Headers(call[1].headers).get("Authorization")).toBe(
    "Bearer owner.signed.token",
  );
  expect(new Headers(call[1].headers).get("X-CAO-Browser")).toBe("1");
  expect(call[1].credentials).toBe("same-origin");
  expect(JSON.parse(call[1].body)).toEqual({
    username: "felni",
    password: "TenChars1!",
    remember: false,
  });
  expect(sessionStorage.getItem("cao.browser.bearer")).toBeNull();
});
it("reports rejected setup without server details or retaining passwords", async () => {
  sessionStorage.setItem("cao.browser.bearer", "owner.signed.token");
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(json(setupConfig))
    .mockResolvedValueOnce(
      json(
        {
          detail: {
            code: "invalid_bearer",
            message: "sensitive server detail",
          },
        },
        401,
      ),
    );
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  await screen.findByLabelText("Usuario");
  fireEvent.click(screen.getByRole("tab", { name: "Crear cuenta" }));
  await screen.findByLabelText("Usuario");
  fireEvent.change(screen.getByLabelText("Contraseña"), {
    target: { value: "a long secret password" },
  });
  fireEvent.change(screen.getByLabelText("Confirmar contraseña"), {
    target: { value: "a long secret password" },
  });
  fireEvent.submit(screen.getByLabelText("Usuario").closest("form")!);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "enlace de acceso autorizado",
  );
  expect(screen.getByLabelText("Contraseña")).toHaveValue("");
  expect(screen.getByLabelText("Confirmar contraseña")).toHaveValue("");
  expect(screen.queryByText("Protected dashboard")).toBeNull();
  expect(screen.queryByText(/sensitive server detail/)).toBeNull();
});
it("requires the authorized access link before sending account creation", async () => {
  const fetch = vi.fn().mockResolvedValue(json(setupConfig));
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  await screen.findByLabelText("Usuario");
  fireEvent.click(screen.getByRole("tab", { name: "Crear cuenta" }));
  await screen.findByLabelText("Usuario");
  fireEvent.change(screen.getByLabelText("Contraseña"), {
    target: { value: "a long secret password" },
  });
  fireEvent.change(screen.getByLabelText("Confirmar contraseña"), {
    target: { value: "a long secret password" },
  });
  fireEvent.submit(screen.getByLabelText("Usuario").closest("form")!);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "enlace de acceso autorizado",
  );
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("rejects nine characters before submitting account setup", async () => {
  sessionStorage.setItem("cao.browser.bearer", "owner.signed.token");
  const fetch = vi.fn().mockResolvedValue(json(setupConfig));
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  await screen.findByLabelText("Usuario");
  fireEvent.click(screen.getByRole("tab", { name: "Crear cuenta" }));
  await screen.findByLabelText("Usuario");
  for (const label of ["Contraseña", "Confirmar contraseña"])
    fireEvent.change(screen.getByLabelText(label), {
      target: { value: "123456789" },
    });
  fireEvent.submit(screen.getByLabelText("Usuario").closest("form")!);
  expect(await screen.findByRole("alert")).toHaveTextContent("entre 10 y 128");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each([false, true])(
  "checks setup publication without replaying creation and fails closed when unavailable (%s)",
  async (unavailable) => {
    sessionStorage.setItem("cao.browser.bearer", "owner.signed.token");
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(setupConfig))
      .mockResolvedValueOnce(
        json(
          {
            detail: {
              code: "setup_publication_uncertain",
              message: "internal fsync detail",
            },
          },
          503,
        ),
      )
      .mockResolvedValueOnce(
        unavailable
          ? json({ detail: { code: "auth_unavailable" } }, 503)
          : json({ mode: "local_password" }),
      )
      .mockResolvedValueOnce(
        json({ detail: { code: "session_required" } }, 401),
      );
    vi.stubGlobal("fetch", fetch);
    render(<App />);
    await screen.findByLabelText("Usuario");
    fireEvent.click(screen.getByRole("tab", { name: "Crear cuenta" }));
    for (const label of ["Contraseña", "Confirmar contraseña"])
      fireEvent.change(screen.getByLabelText(label), {
        target: { value: "TenChars1!" },
      });
    fireEvent.submit(screen.getByLabelText("Usuario").closest("form")!);
    const check = await screen.findByRole("button", {
      name: "Ir a iniciar sesión",
    });
    expect(screen.getByLabelText("Contraseña")).toHaveValue("");
    expect(
      screen.getByRole("button", { name: "Crear cuenta y entrar" }),
    ).toBeDisabled();
    expect(screen.queryByText(/internal fsync detail/)).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(2);
    fireEvent.click(check);
    if (unavailable) {
      expect(
        await screen.findByRole("button", { name: "Reintentar conexión" }),
      ).toBeInTheDocument();
      expect(fetch.mock.calls.map((call) => call[0])).toEqual([
        "/auth/config",
        "/auth/setup",
        "/auth/config",
      ]);
    } else {
      expect(
        await screen.findByRole("heading", { name: "Iniciar sesión" }),
      ).toBeInTheDocument();
      expect(fetch.mock.calls.map((call) => call[0])).toEqual([
        "/auth/config",
        "/auth/setup",
        "/auth/config",
        "/auth/session",
      ]);
    }
    expect(
      fetch.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(1);
    expect(screen.queryByText("Protected dashboard")).toBeNull();
  },
);

it("allows explicit account creation after a confirmed pending setup without replaying the uncertain request", async () => {
  sessionStorage.setItem("cao.browser.bearer", "owner.signed.token");
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(json(setupConfig))
    .mockResolvedValueOnce(
      json({ detail: { code: "setup_publication_uncertain" } }, 503),
    )
    .mockResolvedValueOnce(json(setupConfig));
  vi.stubGlobal("fetch", fetch);
  await initializeBrowserAuth();
  render(<BrowserSetup />);
  for (const label of ["Contraseña", "Confirmar contraseña"])
    fireEvent.change(screen.getByLabelText(label), {
      target: { value: "TenChars1!" },
    });
  fireEvent.submit(screen.getByLabelText("Usuario").closest("form")!);
  fireEvent.click(
    await screen.findByRole("button", { name: "Ir a iniciar sesión" }),
  );
  await vi.waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Crear cuenta y entrar" }),
    ).toBeEnabled(),
  );
  expect(screen.getByLabelText("Contraseña")).toHaveValue("");
  expect(screen.getByLabelText("Confirmar contraseña")).toHaveValue("");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Ir a iniciar sesión" }),
  ).toBeNull();
  expect(fetch.mock.calls.map((call) => call[0])).toEqual([
    "/auth/config",
    "/auth/setup",
    "/auth/config",
  ]);
  expect(
    fetch.mock.calls.filter((call) => call[1]?.method === "POST"),
  ).toHaveLength(1);
});
