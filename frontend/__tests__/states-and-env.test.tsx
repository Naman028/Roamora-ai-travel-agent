import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CurrentTripEmptyState } from "@/components/itinerary/current-trip-empty-state";
import { SavedTripsEmptyState } from "@/components/saved-trips/saved-trips-empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { clientEnvSchema } from "@/lib/env/schemas";
import { backendEnvInputSchema } from "@/server/env/backend-env";

describe("truthful empty and error states", () => {
  it("explains that current-trip integration is pending", () => {
    render(<CurrentTripEmptyState />);
    expect(
      screen.getByRole("heading", { name: "No trip in motion yet" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Planning tools coming soon" }),
    ).toBeDisabled();
  });

  it("does not imply that saved trips persist yet", () => {
    render(<SavedTripsEmptyState />);
    expect(screen.getByRole("heading", { name: "A fresh page" })).toBeVisible();
    expect(screen.getByText(/once secure saving is connected/i)).toBeVisible();
  });

  it("renders a safe error without exposing exception content", () => {
    render(
      <ErrorState
        title="A safe title"
        description="A safe explanation."
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("A safe title");
    expect(screen.getByRole("alert")).not.toHaveTextContent("stack");
  });
});

describe("environment schemas", () => {
  it("defaults the public app environment", () => {
    expect(clientEnvSchema.parse({})).toEqual({
      NEXT_PUBLIC_APP_ENV: "development",
    });
  });

  it("requires a valid backend URL and non-empty server secret", () => {
    expect(() =>
      backendEnvInputSchema.parse({
        BACKEND_API_URL: "not-a-url",
        BACKEND_API_KEY: "",
      }),
    ).toThrow();
    expect(() => backendEnvInputSchema.parse({})).toThrow();
  });
});
