"use client";

import { Bookmark, Map, MessageCircle } from "lucide-react";
import type { AppPanel } from "@/lib/domain/models";

const items: ReadonlyArray<{
  panel: AppPanel;
  label: string;
  icon: typeof MessageCircle;
}> = [
  { panel: "current-trip", label: "Current trip", icon: Map },
  { panel: "chat", label: "Chat", icon: MessageCircle },
  { panel: "saved-trips", label: "Saved trips", icon: Bookmark },
];

export function MobileNavigation({
  activePanel,
  onChange,
}: {
  activePanel: AppPanel;
  onChange: (panel: AppPanel) => void;
}) {
  return (
    <nav className="mobile-navigation" aria-label="Workspace panels">
      {items.map(({ panel, label, icon: Icon }) => {
        const selected = activePanel === panel;
        return (
          <button
            key={panel}
            type="button"
            aria-current={selected ? "page" : undefined}
            onClick={() => onChange(panel)}
          >
            <Icon size={19} aria-hidden="true" />
            <span>{label}</span>
          </button>
        );
      })}
    </nav>
  );
}
