import type { ChatMessage } from "@/features/chat/types/chat.types";

export function MessageBubble({ message }: { message: ChatMessage }) {
  return (
    <article
      className={`message message--${message.role}`}
      aria-label={`${message.role === "assistant" ? "Roamora" : "You"} said`}
    >
      {message.role === "assistant" ? (
        <span className="mini-bot" aria-hidden="true">
          <span />
        </span>
      ) : null}
      <div className="message__bubble">
        <p>{message.content}</p>
        {message.status === "pending" ? (
          <span className="message__meta">Planning…</span>
        ) : null}
        {message.status === "failed" ? (
          <span className="message__meta">Not sent successfully</span>
        ) : null}
        {message.status === "cancelled" ? (
          <span className="message__meta">Waiting cancelled</span>
        ) : null}
      </div>
    </article>
  );
}
