import { Map } from "lucide-react";
import { CurrentTripEmptyState } from "@/components/itinerary/current-trip-empty-state";

export function CurrentTripCard() {
  return (
    <aside
      className="side-panel current-trip-panel"
      aria-labelledby="current-trip-title"
      data-testid="current-trip-panel"
    >
      <div className="panel-heading">
        <span className="panel-heading__icon" aria-hidden="true">
          <Map size={18} />
        </span>
        <div>
          <p className="eyebrow">In progress</p>
          <h2 id="current-trip-title">Current trip</h2>
        </div>
      </div>
      <CurrentTripEmptyState />
      <p className="panel-footnote">
        Your plans stay empty until a verified itinerary is available.
      </p>
    </aside>
  );
}
