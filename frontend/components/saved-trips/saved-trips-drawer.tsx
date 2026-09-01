"use client";

import { SavedTripsPanel } from "@/components/saved-trips/saved-trips-panel";
import { Drawer } from "@/components/ui/drawer";

export function SavedTripsDrawer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Saved trips"
      description="A secure home for the journeys you want to revisit."
    >
      <h2 id="saved-trips-drawer-title" className="sr-only">
        Saved trips list
      </h2>
      <SavedTripsPanel compact />
    </Drawer>
  );
}
