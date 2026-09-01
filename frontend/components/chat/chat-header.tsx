import { Bookmark, MoreHorizontal, Sparkles } from "lucide-react";
import { IconButton } from "@/components/ui/icon-button";

export function ChatHeader({
  onOpenSavedTrips,
}: {
  onOpenSavedTrips: () => void;
}) {
  return (
    <header className="chat-header">
      <div className="brand-lockup">
        <span className="bot-avatar" aria-hidden="true">
          <span className="bot-avatar__face">
            <i />
            <i />
          </span>
        </span>
        <div>
          <div className="brand-lockup__title">
            <h1>Roamora AI</h1>
            <Sparkles size={16} aria-hidden="true" />
          </div>
          <p>Thoughtful travel, shaped around you</p>
        </div>
      </div>
      <div className="chat-header__actions">
        <span className="preview-badge">
          <span aria-hidden="true" />
          Foundation ready
        </span>
        <IconButton
          label="Open saved trips"
          className="tablet-saved-trigger"
          onClick={onOpenSavedTrips}
        >
          <Bookmark size={18} />
        </IconButton>
        <IconButton label="More options" disabled>
          <MoreHorizontal size={19} />
        </IconButton>
      </div>
    </header>
  );
}
