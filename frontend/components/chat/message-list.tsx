import { ChatEmptyState } from "@/components/chat/chat-empty-state";
import { MessageBubble } from "@/components/chat/message-bubble";
import type { ChatMessage } from "@/features/chat/types/chat.types";

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  return (
    <div
      className="message-list"
      aria-label="Conversation"
      role="log"
      aria-live="polite"
      aria-relevant="additions"
      aria-busy={messages.some((message) => message.status === "pending")}
    >
      {messages.length === 1 ? <ChatEmptyState /> : null}
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
    </div>
  );
}
