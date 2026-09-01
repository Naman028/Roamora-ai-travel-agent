import { Compass, Sparkles } from "lucide-react";

export function ChatEmptyState() {
  return (
    <section className="chat-welcome" aria-labelledby="welcome-title">
      <div className="chat-welcome__art" aria-hidden="true">
        <Compass size={34} />
        <Sparkles size={17} />
      </div>
      <p className="eyebrow">A thoughtful place to begin</p>
      <h2 id="welcome-title">Where are you dreaming of next?</h2>
      <p>
        Shape an idea in the composer. AI planning will be enabled after the
        backend contract is ready.
      </p>
    </section>
  );
}
