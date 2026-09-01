import { BookmarkCheck, LockKeyhole } from "lucide-react";
import { SavedTripsEmptyState } from "@/components/saved-trips/saved-trips-empty-state";

export function SavedTripsPanel({ compact = false }: { compact?: boolean }) {
  return (
    <aside
      className={compact ? "saved-panel saved-panel--compact" : "side-panel saved-panel"}
      aria-labelledby={compact ? "saved-trips-drawer-title" : "saved-trips-title"}
      data-testid="saved-trips-panel"
    >
      {!compact ? (
        <div className="panel-heading">
          <span className="panel-heading__icon panel-heading__icon--lavender" aria-hidden="true">
            <BookmarkCheck size={18} />
          </span>
          <div>
            <p className="eyebrow">Your collection</p>
            <h2 id="saved-trips-title">Saved trips</h2>
          </div>
        </div>
      ) : null}
      <SavedTripsEmptyState />
      <div className="privacy-note">
        <LockKeyhole size={14} aria-hidden="true" />
        <span>Secure persistence will arrive with account support.</span>
      </div>
    </aside>
  );
}
