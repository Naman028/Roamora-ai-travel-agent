import type { ReactNode } from "react";
import { MobilePanel } from "@/components/app-shell/mobile-panel";
import type { AppPanel } from "@/lib/domain/models";

export function DesktopLayout({
  activePanel,
  currentTrip,
  chat,
  savedTrips,
}: {
  activePanel: AppPanel;
  currentTrip: ReactNode;
  chat: ReactNode;
  savedTrips: ReactNode;
}) {
  return (
    <div className="shell-grid" data-testid="application-layout">
      <MobilePanel
        panel="current-trip"
        activePanel={activePanel}
        className="shell-slot--current"
      >
        {currentTrip}
      </MobilePanel>
      <MobilePanel
        panel="chat"
        activePanel={activePanel}
        className="shell-slot--chat"
      >
        {chat}
      </MobilePanel>
      <MobilePanel
        panel="saved-trips"
        activePanel={activePanel}
        className="shell-slot--saved"
      >
        {savedTrips}
      </MobilePanel>
    </div>
  );
}
