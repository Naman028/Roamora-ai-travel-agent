import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Providers } from "@/app/providers";
import { AppShell } from "@/components/app-shell/app-shell";

describe("AppShell", () => {
  it("renders one workspace landmark and all three semantic panels", () => {
    renderApp();

    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
    expect(
      screen.getByRole("heading", { level: 1, name: "Roamora AI" }),
    ).toBeVisible();
    expect(screen.getByTestId("application-layout")).toHaveClass("shell-grid");
    expect(screen.getByTestId("current-trip-panel")).toBeInTheDocument();
    expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
    expect(screen.getByTestId("saved-trips-panel")).toBeInTheDocument();
  });

  it("marks the selected mobile navigation destination", () => {
    renderApp();
    const navigation = screen.getByRole("navigation", {
      name: "Workspace panels",
    });
    const savedTripsButton = withinNavigation(navigation, "Saved trips");

    fireEvent.click(savedTripsButton);

    expect(savedTripsButton).toHaveAttribute("aria-current", "page");
    expect(withinNavigation(navigation, "Chat")).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("contains no production destination fixture content", () => {
    renderApp();
    const content = document.body.textContent ?? "";

    for (const fixture of [
      "Italy",
      "Japan",
      "Maldives",
      "Santorini",
    ]) {
      expect(content).not.toContain(fixture);
    }
  });
});

function renderApp() {
  return render(
    <Providers>
      <AppShell />
    </Providers>,
  );
}

function withinNavigation(navigation: HTMLElement, name: string) {
  const button = Array.from(navigation.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === name,
  );
  if (!button) {
    throw new Error(`Navigation button not found: ${name}`);
  }
  return button;
}
