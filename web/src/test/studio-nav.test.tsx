import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { StudioContextBar, StudioNav, readerLabel, shortcutLabel, type StudioDestination } from "../components/StudioNav";

type K = "home" | "profiles" | "agents" | "settings";
const dests: StudioDestination<K>[] = [
  { key: "home", label: "Home", icon: null, group: "work" },
  { key: "profiles", label: "Profiles", icon: null, group: "library" },
  { key: "agents", label: "Agents", icon: null, group: "work" },
  { key: "settings", label: "Settings", icon: null, group: "administration" },
];

describe("StudioNav", () => {
  it("groups by objective while keeping shortcuts bound to destination order", () => {
    render(<StudioNav destinations={dests} current="home" locked={false} onSelect={() => {}} />);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["Home", "Agents", "Profiles", "Settings"]);
    expect(screen.getByRole("tab", { name: "Agents" })).toHaveAttribute("title", "Alt+3");
    expect(screen.getByRole("tab", { name: "Profiles" })).toHaveAttribute("aria-keyshortcuts", "Alt+2");
    expect(screen.getByRole("tab", { name: "Profiles" })).toHaveAccessibleDescription("Library");
    expect(screen.getByRole("tablist", { name: "Studio areas" })).toBeInTheDocument();
  });

  it("moves focus with arrow/Home/End keys without changing selection", () => {
    const onSelect = vi.fn();
    render(<StudioNav destinations={dests} current="home" locked={false} onSelect={onSelect} />);
    const home = screen.getByRole("tab", { name: "Home" });
    home.focus();
    fireEvent.keyDown(home, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Agents" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(screen.getByRole("tab", { name: "Settings" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(home).toHaveFocus();
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "Agents" }));
    expect(onSelect).toHaveBeenCalledWith("agents");
  });

  it("shows only positive badges and the lock hint", () => {
    render(<StudioNav destinations={dests} current="home" locked badges={{ agents: 2, profiles: 0 }} onSelect={() => {}} />);
    expect(screen.getByRole("tab", { name: "Agents 2" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("tab", { name: "Profiles" })).toHaveAttribute("title", "Finish or cancel the in-flight save first");
  });

  it("labels shortcuts like the existing strip", () => {
    expect([0, 8, 9, 10, 11, 12].map(shortcutLabel)).toEqual(["Alt+1", "Alt+9", "Alt+0", "Alt+Shift+1", "Alt+Shift+2", null]);
  });
});

describe("StudioContextBar", () => {
  it("states node, reader and that permissions are not summarised", () => {
    render(
      <StudioContextBar
        node="lab-2"
        nodeOnline={false}
        auth={{ mode: "local_password", status: "authenticated", session: { username: "ana" } as never }}
      />,
    );
    const bar = screen.getByRole("region", { name: "Current context" });
    expect(bar).toHaveTextContent("Node: lab-2");
    expect(bar).toHaveTextContent("registry reports offline");
    expect(bar).toHaveTextContent("Signed in as ana");
    expect(bar).toHaveTextContent(/not summarised by the server/);
  });

  it("never claims a sign-in it does not have", () => {
    expect(readerLabel({ mode: "local_password", status: "expired", session: null })).toBe("Not signed in");
    expect(readerLabel({ mode: "disabled", status: "authenticated", session: null })).toMatch(/authentication disabled/);
    expect(readerLabel({ mode: null, status: "initializing", session: null })).toBe("Checking access…");
    render(<StudioContextBar node={null} auth={{ mode: "bearer", status: "authenticated", session: null }} />);
    expect(screen.getByText("This server")).toBeInTheDocument();
  });
});
