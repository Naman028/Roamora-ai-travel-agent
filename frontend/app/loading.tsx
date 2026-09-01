import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <main className="page-state" aria-busy="true" aria-labelledby="loading-title">
      <div className="state-card state-card--wide">
        <p className="eyebrow">Roamora AI</p>
        <h1 id="loading-title">Preparing your travel workspace</h1>
        <div className="loading-grid" aria-hidden="true">
          <Skeleton className="h-52" />
          <Skeleton className="h-72" />
          <Skeleton className="h-52" />
        </div>
        <span className="sr-only" role="status">
          Loading the application
        </span>
      </div>
    </main>
  );
}
