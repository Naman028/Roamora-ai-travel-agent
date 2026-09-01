import { Compass } from "lucide-react";
import { ButtonLink } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main className="page-state">
      <section className="state-card" aria-labelledby="not-found-title">
        <span className="state-icon" aria-hidden="true">
          <Compass size={24} />
        </span>
        <p className="eyebrow">404 · Off the map</p>
        <h1 id="not-found-title">This route doesn’t exist</h1>
        <p>
          The page may have moved, or the address may be incomplete. Let’s
          return to your planning workspace.
        </p>
        <ButtonLink href="/">Back to Roamora</ButtonLink>
      </section>
    </main>
  );
}
