import type { HTMLAttributes, ReactNode } from "react";

export function Card({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & { children: ReactNode }) {
  return (
    <div className={["surface-card", className].filter(Boolean).join(" ")} {...props}>
      {children}
    </div>
  );
}
