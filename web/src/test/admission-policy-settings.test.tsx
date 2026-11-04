import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type {
  AdmissionPolicyRecord,
  AdmissionPolicyState,
  AdmissionPolicyValues,
} from "../admissionPolicyApi";

const policyApiMock = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  revoke: vi.fn(),
  history: vi.fn(),
}));
const appApiMock = vi.hoisted(() => ({
  getFleet: vi.fn(),
  getMemoryStatus: vi.fn(),
  listSessions: vi.fn(),
}));
const browserAuthMock = vi.hoisted(() => ({
  state: {
    mode: "bearer" as const,
    status: "authenticated" as "authenticated" | "anonymous",
    session: null,
  },
  signal: {} as AbortSignal,
  listeners: [] as Array<() => void>,
}));

vi.mock("../admissionPolicyApi", () => {
  class UnsupportedError extends Error {}
  class ProtocolError extends Error {}
  return {
    admissionPolicyApi: policyApiMock,
    AdmissionPolicyUnsupportedError: UnsupportedError,
    AdmissionPolicyProtocolError: ProtocolError,
  };
});

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getFleet: appApiMock.getFleet,
      getMemoryStatus: appApiMock.getMemoryStatus,
      listSessions: appApiMock.listSessions,
    },
  };
});

vi.mock("../auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../auth")>();
  return {
    ...actual,
    authSnapshot: () => browserAuthMock.state,
    browserAuthSignal: () => browserAuthMock.signal,
    subscribeBrowserAuth: (listener: () => void) => {
      browserAuthMock.listeners.push(listener);
      return () => {
        browserAuthMock.listeners = browserAuthMock.listeners.filter(
          (registered) => registered !== listener,
        );
      };
    },
    initializeBrowserAuth: vi.fn().mockResolvedValue(undefined),
  };
});

vi.mock("../components/DashboardHome", () => ({
  DashboardHome: () => <div>Inicio de prueba</div>,
}));
vi.mock("../components/NodeSwitcher", () => ({ NodeSwitcher: () => null }));
vi.mock("../components/SettingsPanel", () => ({
  SettingsPanel: () => <div>Configuración general existente</div>,
}));

import { AdmissionPolicyUnsupportedError } from "../admissionPolicyApi";
import App from "../App";
import { AdmissionPolicySettings } from "../components/AdmissionPolicySettings";
import { getNodeEpoch, setActiveNode } from "../api";
import { useStore } from "../store";

const POLICY: AdmissionPolicyValues = {
  memory_per_delivery_bytes: 67_108_864n,
  cpu_per_delivery_millicores: 250n,
  memory_reserve_percent: 20n,
  cpu_reserve_percent: 20n,
  max_sample_age_s: 3,
  postgres_reserve_percent: 20n,
  postgres_statement_timeout_ms: 500n,
  sample_interval_s: 1,
  healthy_samples_before_growth: 3n,
};

function record(
  overrides: Partial<AdmissionPolicyRecord> = {},
): AdmissionPolicyRecord {
  return {
    profile_id: "profile-local",
    revision: 9n,
    active: true,
    policy: POLICY,
    actor_principal_id: "actor-hash",
    recorded_at: "2026-11-04T10:00:00Z",
    ...overrides,
  };
}

function state(
  persisted: AdmissionPolicyRecord | null = record(),
  appliedRevision: bigint | null = 8n,
): AdmissionPolicyState {
  return { persisted, applied_revision: appliedRevision };
}

function apiError(status: number) {
  return Object.assign(new Error("private server detail must not be shown"), {
    status,
    detail: "private server detail must not be shown",
  });
}

function changeValue(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function publishBearerAuth(
  status: "authenticated" | "anonymous",
  signal: AbortSignal,
) {
  browserAuthMock.state = { mode: "bearer", status, session: null };
  browserAuthMock.signal = signal;
  for (const listener of [...browserAuthMock.listeners]) listener();
}

let sessionCounter = 0;
let testSessionKey = "policy-test-0";

function renderPanel() {
  return render(<AdmissionPolicySettings sessionKey={testSessionKey} />);
}

async function waitForPolicyReady() {
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Revisar valores" }),
    ).toBeEnabled(),
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  browserAuthMock.state = {
    mode: "bearer",
    status: "authenticated",
    session: null,
  };
  browserAuthMock.signal = {} as AbortSignal;
  browserAuthMock.listeners = [];
  appApiMock.getFleet.mockRejectedValue(
    Object.assign(new Error("no fleet"), { status: 404 }),
  );
  appApiMock.getMemoryStatus.mockResolvedValue({ enabled: false });
  appApiMock.listSessions.mockResolvedValue([]);
  testSessionKey = `policy-test-${++sessionCounter}`;
  setActiveNode(null);
  useStore.setState({
    activeNode: null,
    nodeEpoch: getNodeEpoch(),
    fleetMode: false,
    fleetError: null,
    navLockCount: 0,
    fleetNodes: [],
    sessions: [],
    connected: false,
  });
  policyApiMock.get.mockResolvedValue(state());
  policyApiMock.update.mockResolvedValue(record({ revision: 10n }));
  policyApiMock.revoke.mockResolvedValue(
    record({ revision: 10n, active: false }),
  );
  policyApiMock.history.mockResolvedValue({
    entries: [],
    next_before_revision: null,
  });
});

afterEach(() => {
  useStore.setState({ navLockCount: 0 });
});

describe("AdmissionPolicySettings", () => {
  it("reviews all exact values and the expected revision before sending a PUT", async () => {
    const saved = record({
      revision: 10n,
      policy: { ...POLICY, memory_per_delivery_bytes: 9_007_199_254_740_997n },
    });
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockResolvedValueOnce(state(saved, 9n));
    policyApiMock.update.mockResolvedValueOnce(saved);
    renderPanel();

    await screen.findByText("profile-local");
    changeValue(/Memoria estimada por entrega/, "9007199254740997");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));

    expect(
      screen.getByRole("heading", { name: "Revisión antes de guardar" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/9007199254740997 bytes/)).toBeInTheDocument();
    expect(screen.getByText(/revisión esperada 9/)).toBeInTheDocument();
    expect(policyApiMock.update).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar y guardar" }),
    );

    await waitFor(() => expect(policyApiMock.update).toHaveBeenCalledTimes(1));
    expect(policyApiMock.update).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_revision: 9n,
        policy: expect.objectContaining({
          memory_per_delivery_bytes: 9_007_199_254_740_997n,
        }),
      }),
      expect.any(AbortSignal),
    );
    expect(
      await screen.findByText(
        /Guardado confirmado: perfil local profile-local, revisión 10/,
      ),
    ).toBeInTheDocument();
  });

  it("requires every explicit value when no policy has been saved", async () => {
    policyApiMock.get.mockResolvedValueOnce(state(null, null));
    renderPanel();

    await screen.findByText("Aún no existe una política persistida");
    expect(
      screen.getByText(
        /Una revisión aplicada desconocida no acredita capacidad/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "",
    );
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));

    expect(screen.getAllByText("Este valor es obligatorio.")).toHaveLength(9);
    expect(policyApiMock.update).not.toHaveBeenCalled();
  });

  it("uses BigInt bounds and rejects a sample interval at the freshness limit", async () => {
    renderPanel();
    await screen.findByText("profile-local");
    changeValue(/Memoria estimada por entrega/, "9223372036854775808");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    expect(
      screen.getByText(
        "El entero debe caber en un entero con signo de 64 bits.",
      ),
    ).toBeInTheDocument();

    changeValue(/Memoria estimada por entrega/, "67108864");
    changeValue(/Intervalo de muestreo/, "3");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    expect(
      screen.getByText(
        "Debe ser menor que la antigüedad máxima de una muestra.",
      ),
    ).toBeInTheDocument();
    expect(policyApiMock.update).not.toHaveBeenCalled();
  });

  it("preserves a 409 draft and requires explicit reload before another review", async () => {
    const draftValue = "9007199254740997";
    policyApiMock.update.mockRejectedValueOnce(apiError(409));
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockResolvedValueOnce(state(record({ revision: 10n }), 8n));
    renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();
    changeValue(/Memoria estimada por entrega/, draftValue);
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar y guardar" }),
    );

    const reload = await screen.findByRole("button", {
      name: "Cargar política actual",
    });
    const retainedDraft = screen.getByLabelText(/Memoria estimada por entrega/);
    expect(retainedDraft).toHaveValue(draftValue);
    expect(retainedDraft).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Confirmar y guardar" }),
    ).toBeNull();
    expect(screen.queryByText(/private server detail/)).toBeNull();
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);

    fireEvent.click(reload);
    await waitFor(() => expect(policyApiMock.get).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Revisar valores" }),
      ).toBeEnabled(),
    );
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      draftValue,
    );
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    expect(screen.getByText(/revisión esperada 10/)).toBeInTheDocument();
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Descartar borrador" }));
  });

  it("requires manual status inspection after an uncertain write and never retries it", async () => {
    policyApiMock.update.mockRejectedValueOnce(
      new Error("private network failure"),
    );
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockResolvedValueOnce(state(record({ revision: 10n }), 8n));
    renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();
    changeValue(/Memoria estimada por entrega/, "67108865");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar y guardar" }),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Inspeccionar estado actual" }),
    );
    await waitFor(() => expect(policyApiMock.get).toHaveBeenCalledTimes(2));
    await waitForPolicyReady();
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "67108865",
    );
    expect(screen.queryByText(/private network failure/)).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Confirmar y guardar" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Descartar borrador" }));
  });

  it("does not let a refresh started before a save replace its acknowledged revision", async () => {
    const saved = record({
      revision: 10n,
      policy: { ...POLICY, memory_per_delivery_bytes: 67_108_865n },
    });
    let finishRefresh!: (fresh: AdmissionPolicyState) => void;
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockImplementationOnce(
        () => new Promise((resolve) => (finishRefresh = resolve)),
      )
      .mockResolvedValueOnce(state(saved, 9n));
    policyApiMock.update.mockResolvedValueOnce(saved);
    renderPanel();
    await screen.findByText("profile-local");

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Actualizar estado" }),
      ).toBeEnabled(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Actualizar estado" }));
    await waitFor(() => expect(policyApiMock.get).toHaveBeenCalledTimes(2));
    changeValue(/Memoria estimada por entrega/, "67108865");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    expect(policyApiMock.update).not.toHaveBeenCalled();
    if (screen.queryByRole("heading", { name: "Revisión antes de guardar" })) {
      fireEvent.click(
        screen.getByRole("button", { name: "Confirmar y guardar" }),
      );
    }

    if (policyApiMock.update.mock.calls.length > 0) {
      expect(
        await screen.findByText(
          /Guardado confirmado: perfil local profile-local, revisión 10/,
        ),
      ).toBeInTheDocument();
      await act(async () => finishRefresh(state(record({ revision: 9n }), 8n)));
      await waitFor(() =>
        expect(
          screen.getByText("Revisión persistida").parentElement?.textContent,
        ).toContain("10"),
      );
      expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
        "67108865",
      );
      expect(policyApiMock.update).toHaveBeenCalledTimes(1);
      return;
    }

    await act(async () => finishRefresh(state(record({ revision: 9n }), 8n)));
    expect(policyApiMock.update).not.toHaveBeenCalled();
    changeValue(/Memoria estimada por entrega/, "67108865");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    expect(screen.getByText(/revisión esperada 9/)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar y guardar" }),
    );
    expect(
      await screen.findByText(
        /Guardado confirmado: perfil local profile-local, revisión 10/,
      ),
    ).toBeInTheDocument();
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);
  });

  it("does not let an open revoke confirmation survive a conflicting edit and review", async () => {
    const revoked = record({ revision: 10n, active: false });
    policyApiMock.revoke.mockResolvedValueOnce(revoked);
    policyApiMock.get.mockResolvedValueOnce(state());
    renderPanel();
    await screen.findByText("profile-local");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Revocar política" }),
      ).toBeEnabled(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Revocar política" }));
    const memoryInput = screen.getByLabelText(/Memoria estimada por entrega/);
    const inputLockedDuringConfirmation = (memoryInput as HTMLInputElement)
      .disabled;
    fireEvent.change(memoryInput, { target: { value: "67108865" } });

    if ((memoryInput as HTMLInputElement).value !== "67108865") {
      expect(memoryInput).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
      fireEvent.change(memoryInput, { target: { value: "67108865" } });
    }
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));

    const staleConfirmation = screen.queryByRole("button", {
      name: "Confirmar revocación",
    });
    if (staleConfirmation) fireEvent.click(staleConfirmation);

    expect(
      screen.getByRole("heading", { name: "Revisión antes de guardar" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Confirmar revocación" }),
    ).toBeNull();
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "67108865",
    );
    expect(inputLockedDuringConfirmation).toBe(true);
    expect(policyApiMock.revoke).not.toHaveBeenCalled();
    expect(policyApiMock.update).not.toHaveBeenCalled();
  });

  it("keeps an acknowledged PUT successful when the optional status read fails", async () => {
    const saved = record({ revision: 10n });
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockRejectedValueOnce(new Error("private status read detail"));
    policyApiMock.update.mockResolvedValueOnce(saved);
    renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();
    changeValue(/Memoria estimada por entrega/, "67108865");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar y guardar" }),
    );

    expect(
      await screen.findByText(
        /Guardado confirmado: perfil local profile-local, revisión 10/,
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/No se pudo actualizar la lectura de aplicación/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Revisión aplicada").parentElement,
    ).toHaveTextContent("Desconocida");
    expect(screen.queryByText(/private status read detail/)).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Inspeccionar estado actual" }),
    ).toBeNull();
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);
  });

  it("confirms revocation and keeps the bounded history visible", async () => {
    const oldEntry = record({ revision: 7n });
    const revoked = record({ revision: 10n, active: false });
    policyApiMock.history.mockResolvedValueOnce({
      entries: [oldEntry],
      next_before_revision: null,
    });
    policyApiMock.revoke.mockResolvedValueOnce(revoked);
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockResolvedValueOnce(state(revoked, 8n));
    renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();
    fireEvent.click(screen.getByRole("button", { name: "Cargar historial" }));
    await screen.findByText("Revisión 7 · activa");
    changeValue(/Memoria estimada por entrega/, "67108865");
    expect(
      screen.getByRole("button", { name: "Revocar política" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Descartar borrador" }));
    fireEvent.click(screen.getByRole("button", { name: "Revocar política" }));
    expect(policyApiMock.revoke).not.toHaveBeenCalled();
    expect(
      screen.getByRole("heading", { name: "Confirmar revocación" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar revocación" }),
    );

    await waitFor(() =>
      expect(policyApiMock.revoke).toHaveBeenCalledWith(
        { expected_revision: 9n },
        expect.any(AbortSignal),
      ),
    );
    expect(
      await screen.findByText(/Revocación confirmada/),
    ).toBeInTheDocument();
    expect(screen.getByText("Revisión 7 · activa")).toBeInTheDocument();
  });

  it("does not carry the old applied revision after an acknowledged revoke", async () => {
    const revoked = record({ revision: 10n, active: false });
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockRejectedValueOnce(new Error("private status read detail"));
    policyApiMock.revoke.mockResolvedValueOnce(revoked);
    renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();
    fireEvent.click(screen.getByRole("button", { name: "Revocar política" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar revocación" }),
    );

    expect(
      await screen.findByText(/Revocación confirmada/),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/No se pudo actualizar la lectura de aplicación/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Revisión persistida").parentElement,
    ).toHaveTextContent("10");
    expect(
      screen.getByText("Revisión aplicada").parentElement,
    ).toHaveTextContent("Desconocida");
    expect(policyApiMock.revoke).toHaveBeenCalledTimes(1);
  });

  it("loads fresh values after a revoke conflict when the form was clean", async () => {
    const current = record({
      revision: 10n,
      policy: { ...POLICY, memory_per_delivery_bytes: 70_000_000n },
    });
    policyApiMock.revoke.mockRejectedValueOnce(apiError(409));
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockResolvedValueOnce(state(current, 9n));
    renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();

    fireEvent.click(screen.getByRole("button", { name: "Revocar política" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar revocación" }),
    );
    await screen.findByRole("button", { name: "Cargar política actual" });
    fireEvent.click(
      screen.getByRole("button", { name: "Cargar política actual" }),
    );
    await waitForPolicyReady();

    expect(
      screen.getByText("Revisión persistida").parentElement,
    ).toHaveTextContent("10");
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "70000000",
    );
    expect(policyApiMock.revoke).toHaveBeenCalledTimes(1);
    expect(policyApiMock.update).not.toHaveBeenCalled();
  });

  it("allows leaving after a revoke conflict when the required inspection is unavailable", async () => {
    policyApiMock.revoke.mockRejectedValueOnce(apiError(409));
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockRejectedValueOnce(new Error("backend unavailable"));
    renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();
    fireEvent.click(screen.getByRole("button", { name: "Revocar política" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar revocación" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Cargar política actual" }),
    );
    await screen.findByText(
      "No se pudo leer la política. Comprueba la conexión y vuelve a intentarlo.",
    );

    await waitFor(() => expect(useStore.getState().navLockCount).toBe(1));
    const allowExit = screen.getByRole("button", {
      name: "Permitir salir conservando el estado",
    });
    await waitFor(() => expect(allowExit).toBeEnabled());
    fireEvent.click(allowExit);
    await waitFor(() => expect(useStore.getState().navLockCount).toBe(0));
    expect(policyApiMock.revoke).toHaveBeenCalledTimes(1);
    expect(policyApiMock.update).not.toHaveBeenCalled();
  });

  it("allows an explicit exit after uncertain write while retaining the draft and inspection gate", async () => {
    policyApiMock.update.mockRejectedValueOnce(
      new Error("private network failure"),
    );
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockRejectedValueOnce(new Error("backend unavailable"))
      .mockResolvedValueOnce(state());
    const { unmount } = renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();
    changeValue(/Memoria estimada por entrega/, "67108865");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar y guardar" }),
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Inspeccionar estado actual",
      }),
    );
    await screen.findByText(
      "No se pudo leer la política. Comprueba la conexión y vuelve a intentarlo.",
    );
    expect(policyApiMock.get).toHaveBeenCalledTimes(2);

    await waitFor(() => expect(useStore.getState().navLockCount).toBe(1));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Permitir salir conservando el estado",
      }),
    );
    await waitFor(() => expect(useStore.getState().navLockCount).toBe(0));
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);

    unmount();
    renderPanel();
    expect(
      screen.getByRole("button", { name: "Inspeccionar estado actual" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Revisar valores" }),
    ).toBeNull();
    expect(policyApiMock.get).toHaveBeenCalledTimes(2);
    fireEvent.click(
      screen.getByRole("button", { name: "Inspeccionar estado actual" }),
    );
    await waitForPolicyReady();
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "67108865",
    );
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);
    expect(policyApiMock.get).toHaveBeenCalledTimes(3);
  });

  it("pages history with a bounded keyset cursor", async () => {
    const first = record({ revision: 6n });
    const second = record({ revision: 5n });
    policyApiMock.history
      .mockResolvedValueOnce({ entries: [first], next_before_revision: 5n })
      .mockResolvedValueOnce({ entries: [second], next_before_revision: null });
    renderPanel();
    await screen.findByText("profile-local");
    fireEvent.click(screen.getByRole("button", { name: "Cargar historial" }));
    await screen.findByText("Revisión 6 · activa");
    fireEvent.click(
      screen.getByRole("button", { name: "Cargar revisiones anteriores" }),
    );

    await screen.findByText("Revisión 5 · activa");
    expect(policyApiMock.history).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ limit: 20, signal: expect.any(AbortSignal) }),
    );
    expect(policyApiMock.history).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        limit: 20,
        before_revision: 5n,
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("does not fetch policy for a remote node and requires explicit local selection", async () => {
    setActiveNode("remote-worker");
    useStore.setState({
      activeNode: "remote-worker",
      nodeEpoch: getNodeEpoch(),
      navLockCount: 0,
    });
    renderPanel();

    expect(
      screen.getByText(
        /El nodo remoto seleccionado no recibe esta configuración/,
      ),
    ).toBeInTheDocument();
    expect(policyApiMock.get).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Seleccionar servidor local" }),
    );

    await screen.findByText("profile-local");
    expect(useStore.getState().activeNode).toBeNull();
    expect(policyApiMock.get).toHaveBeenCalledTimes(1);
  });

  it("does not publish a policy response that arrives after node context changes", async () => {
    let resolveRead!: (value: AdmissionPolicyState) => void;
    policyApiMock.get.mockImplementationOnce(
      () => new Promise((resolve) => (resolveRead = resolve)),
    );
    renderPanel();
    await waitFor(() => expect(policyApiMock.get).toHaveBeenCalledTimes(1));
    const signal = policyApiMock.get.mock.calls[0][0] as AbortSignal;

    act(() => useStore.getState().selectNode("remote-worker"));
    expect(
      screen.getByText(
        /El nodo remoto seleccionado no recibe esta configuración/,
      ),
    ).toBeInTheDocument();
    expect(signal.aborted).toBe(true);

    await act(async () => resolveRead(state()));
    expect(screen.queryByText("profile-local")).toBeNull();
    expect(
      screen.getByText(
        /El nodo remoto seleccionado no recibe esta configuración/,
      ),
    ).toBeInTheDocument();
  });

  it("shows a fixed admin denial without exposing the backend body", async () => {
    policyApiMock.get.mockRejectedValueOnce(apiError(403));
    renderPanel();

    expect(
      await screen.findByText(
        "Tu sesión no tiene permiso de administrador para esta política.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/private server detail/)).toBeNull();
    expect(policyApiMock.update).not.toHaveBeenCalled();
  });

  it("shows a fixed admin denial after a rejected write and keeps the draft", async () => {
    policyApiMock.update.mockRejectedValueOnce(apiError(403));
    renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();
    changeValue(/Memoria estimada por entrega/, "67108865");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar y guardar" }),
    );

    expect(
      await screen.findByText(
        "Tu sesión no tiene permiso de administrador para esta política.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "67108865",
    );
    expect(screen.queryByText(/private server detail/)).toBeNull();
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: "Inspeccionar estado actual" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Descartar borrador" }));
  });

  it("shows the unsupported exact-integer message without offering a draft", async () => {
    policyApiMock.get.mockRejectedValueOnce(
      new AdmissionPolicyUnsupportedError(),
    );
    renderPanel();

    expect(
      await screen.findByText(/no es compatible con la representación exacta/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Revisar valores" }),
    ).toBeNull();
    expect(policyApiMock.update).not.toHaveBeenCalled();
  });

  it("locks navigation while a draft is active and releases it on explicit discard", async () => {
    renderPanel();
    await screen.findByText("profile-local");
    changeValue(/Memoria estimada por entrega/, "67108865");

    await waitFor(() => expect(useStore.getState().navLockCount).toBe(1));
    act(() => useStore.getState().selectNode("remote-worker"));
    expect(useStore.getState().activeNode).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Descartar borrador" }));
    await waitFor(() => expect(useStore.getState().navLockCount).toBe(0));
  });

  it("keeps a pending write marked for inspection if the component unmounts", async () => {
    let rejectWrite!: (error: Error) => void;
    policyApiMock.update.mockImplementationOnce(
      () => new Promise((_resolve, reject) => (rejectWrite = reject)),
    );
    policyApiMock.get
      .mockResolvedValueOnce(state())
      .mockRejectedValueOnce(new Error("backend unavailable"))
      .mockResolvedValueOnce(state());
    const { unmount } = renderPanel();
    await screen.findByText("profile-local");
    await waitForPolicyReady();
    changeValue(/Memoria estimada por entrega/, "67108865");
    fireEvent.click(screen.getByRole("button", { name: "Revisar valores" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar y guardar" }),
    );
    await waitFor(() => expect(policyApiMock.update).toHaveBeenCalledTimes(1));

    unmount();
    expect(useStore.getState().navLockCount).toBe(0);
    const recovered = renderPanel();
    expect(
      screen.getByRole("button", { name: "Inspeccionar estado actual" }),
    ).toBeInTheDocument();
    expect(policyApiMock.get).toHaveBeenCalledTimes(1);

    rejectWrite(new Error("ambiguous write"));
    await act(async () => Promise.resolve());
    fireEvent.click(
      screen.getByRole("button", { name: "Inspeccionar estado actual" }),
    );
    await screen.findByText(
      "No se pudo leer la política. Comprueba la conexión y vuelve a intentarlo.",
    );
    expect(policyApiMock.get).toHaveBeenCalledTimes(2);
    const allowExit = screen.getByRole("button", {
      name: "Permitir salir conservando el estado",
    });
    await waitFor(() => expect(allowExit).toBeEnabled());
    fireEvent.click(allowExit);
    await waitFor(() => expect(useStore.getState().navLockCount).toBe(0));
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);

    recovered.unmount();
    renderPanel();
    expect(
      screen.getByRole("button", { name: "Inspeccionar estado actual" }),
    ).toBeInTheDocument();
    expect(policyApiMock.get).toHaveBeenCalledTimes(2);
    fireEvent.click(
      screen.getByRole("button", { name: "Inspeccionar estado actual" }),
    );
    await waitForPolicyReady();
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "67108865",
    );
    expect(policyApiMock.update).toHaveBeenCalledTimes(1);
    expect(policyApiMock.get).toHaveBeenCalledTimes(3);
  });

  it("does not carry a local draft into a different authenticated session", async () => {
    const { rerender } = render(
      <AdmissionPolicySettings sessionKey="session-one" />,
    );
    await screen.findByText("profile-local");
    changeValue(/Memoria estimada por entrega/, "67108865");

    rerender(
      <AdmissionPolicySettings key="session-two" sessionKey="session-two" />,
    );
    await screen.findByText("profile-local");

    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "67108864",
    );
    expect(useStore.getState().navLockCount).toBe(0);
  });

  it("isolates bearer drafts when the browser auth authority changes", async () => {
    const firstAuthority = {} as AbortSignal;
    browserAuthMock.state = {
      mode: "bearer",
      status: "authenticated",
      session: null,
    };
    browserAuthMock.signal = firstAuthority;
    render(<App />);

    fireEvent.click(await screen.findByRole("tab", { name: "Settings" }));
    await screen.findByText("profile-local");
    changeValue(/Memoria estimada por entrega/, "67108865");
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "67108865",
    );

    act(() => publishBearerAuth("authenticated", firstAuthority));
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "67108865",
    );

    const secondAuthority = {} as AbortSignal;
    act(() => publishBearerAuth("anonymous", secondAuthority));
    await screen.findByText("Comprobando nodos…");
    act(() => publishBearerAuth("authenticated", secondAuthority));

    fireEvent.click(await screen.findByRole("tab", { name: "Settings" }));
    await screen.findByText("profile-local");
    expect(screen.getByLabelText(/Memoria estimada por entrega/)).toHaveValue(
      "67108864",
    );
  });
});
