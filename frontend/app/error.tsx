"use client";

import { ErrorState } from "@/components/ui/error-state";

export default function ErrorPage({
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  return (
    <main className="page-state">
      <ErrorState
        title="Your travel workspace hit a detour"
        description="We couldn’t load this view safely. Try again, or return later if the problem continues."
        onRetry={unstable_retry}
      />
    </main>
  );
}
