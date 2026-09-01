import { MapPinned } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { RouteTimeline } from "@/components/itinerary/route-timeline";
import type { EmptyTripState } from "@/lib/domain/models";

const emptyTrip: EmptyTripState = {
  kind: "empty",
  title: "No trip in motion yet",
  description:
    "When itinerary integration is ready, the route you’re planning will take shape here.",
};

export function CurrentTripEmptyState() {
  return (
    <div className="trip-empty">
      <RouteTimeline />
      <EmptyState
        icon={<MapPinned size={22} />}
        title={emptyTrip.title}
        description={emptyTrip.description}
        action={
          <Button variant="secondary" disabled>
            Planning tools coming soon
          </Button>
        }
      />
    </div>
  );
}
