"use client";

import { useCallback, useState } from "react";
import { AmbientBackground } from "@/components/app-shell/ambient-background";
import { DesktopLayout } from "@/components/app-shell/desktop-layout";
import { MobileNavigation } from "@/components/app-shell/mobile-navigation";
import { ChatPanel } from "@/components/chat/chat-panel";
import { CurrentTripCard } from "@/components/itinerary/current-trip-card";
import { SavedTripsDrawer } from "@/components/saved-trips/saved-trips-drawer";
import { SavedTripsPanel } from "@/components/saved-trips/saved-trips-panel";
import type { AppPanel } from "@/lib/domain/models";

export function AppShell() {
  const [activePanel, setActivePanel] = useState<AppPanel>("chat");
  const [savedDrawerOpen, setSavedDrawerOpen] = useState(false);
  const closeSavedDrawer = useCallback(() => setSavedDrawerOpen(false), []);

  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to travel workspace
      </a>
      <AmbientBackground />
      <main id="main-content" className="app-frame">
        <DesktopLayout
          activePanel={activePanel}
          currentTrip={<CurrentTripCard />}
          chat={
            <ChatPanel onOpenSavedTrips={() => setSavedDrawerOpen(true)} />
          }
          savedTrips={<SavedTripsPanel />}
        />
      </main>
      <MobileNavigation
        activePanel={activePanel}
        onChange={setActivePanel}
      />
      <SavedTripsDrawer
        open={savedDrawerOpen}
        onClose={closeSavedDrawer}
      />
    </>
  );
}
