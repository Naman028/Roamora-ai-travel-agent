import type { ReactNode } from "react";
import type { AppPanel } from "@/lib/domain/models";

export function MobilePanel({
  panel,
  activePanel,
  className,
  children,
}: {
  panel: AppPanel;
  activePanel: AppPanel;
  className: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`shell-slot ${className}`}
      data-mobile-active={panel === activePanel ? "true" : "false"}
    >
      {children}
    </div>
  );
}
