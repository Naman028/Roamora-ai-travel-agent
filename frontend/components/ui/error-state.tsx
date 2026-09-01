import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ErrorState({
  title,
  description,
  onRetry,
}: {
  title: string;
  description: string;
  onRetry?: () => void;
}) {
  return (
    <section
      className="state-card"
      role="alert"
      aria-labelledby="error-state-title"
    >
      <span className="state-icon state-icon--error" aria-hidden="true">
        <AlertTriangle size={24} />
      </span>
      <p className="eyebrow">Something went wrong</p>
      <h1 id="error-state-title">{title}</h1>
      <p>{description}</p>
      {onRetry ? <Button onClick={onRetry}>Try again</Button> : null}
    </section>
  );
}
