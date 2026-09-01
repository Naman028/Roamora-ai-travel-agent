import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Drawer } from "@/components/ui/drawer";

function DrawerHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open collection
      </button>
      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        title="Saved journeys"
      >
        <button type="button">First drawer action</button>
      </Drawer>
    </>
  );
}

describe("Drawer", () => {
  it("opens as a modal, closes with Escape, and restores focus", () => {
    render(<DrawerHarness />);
    const trigger = screen.getByRole("button", { name: "Open collection" });
    trigger.focus();
    fireEvent.click(trigger);

    expect(screen.getByRole("dialog")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Close saved trips" }),
    ).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
