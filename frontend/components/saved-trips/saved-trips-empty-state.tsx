import { Bookmark } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";
import type { SavedTripsState } from "@/lib/domain/models";

const savedTrips: SavedTripsState = { kind: "empty", trips: [] };

export function SavedTripsEmptyState() {
  return (
    <EmptyState
      icon={<Bookmark size={22} />}
      title="A fresh page"
      description={
        savedTrips.trips.length === 0
          ? "Trips you choose to keep will appear here once secure saving is connected."
          : "Your saved plans will appear here."
      }
    />
  );
}
