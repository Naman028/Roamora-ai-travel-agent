"use client";

import { ArrowUp, Paperclip, X } from "lucide-react";
import {
  useRef,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { IconButton } from "@/components/ui/icon-button";
import { CHAT_MESSAGE_MAX_LENGTH } from "@/features/chat/contracts/chat-contracts";
import type { ChatState } from "@/features/chat/types/chat.types";

export const MAX_MESSAGE_LENGTH = CHAT_MESSAGE_MAX_LENGTH;

export function ChatComposer({
  value,
  submissionStatus,
  onChange,
  onSubmit,
  onCancel,
}: {
  value: string;
  submissionStatus: ChatState["submissionStatus"];
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  const isSubmitting = submissionStatus === "submitting";

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (value.trim() && !isSubmitting) {
      onSubmit();
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      formRef.current?.requestSubmit();
    }
  }

  return (
    <form
      ref={formRef}
      className="composer"
      onSubmit={handleSubmit}
      aria-label="Travel planning composer"
    >
      <label className="sr-only" htmlFor="travel-message">
        Ask Roamora about your travel idea
      </label>
      <textarea
        id="travel-message"
        name="message"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        maxLength={MAX_MESSAGE_LENGTH}
        rows={1}
        placeholder="Describe the trip you have in mind…"
        aria-describedby="composer-help composer-count composer-status"
      />
      <div className="composer__toolbar">
        <div className="composer__tools">
          <IconButton label="Attachments are not available yet" disabled>
            <Paperclip size={18} />
          </IconButton>
          <span id="composer-count" className="composer__count">
            {value.length.toLocaleString()} /{" "}
            {MAX_MESSAGE_LENGTH.toLocaleString()}
          </span>
        </div>
        {isSubmitting ? (
          <button
            type="button"
            className="composer__send composer__cancel"
            onClick={onCancel}
            aria-label="Cancel travel request"
          >
            <X size={20} aria-hidden="true" />
          </button>
        ) : (
          <button
            type="submit"
            className="composer__send"
            disabled={!value.trim()}
            aria-label="Send travel idea"
          >
            <ArrowUp size={20} aria-hidden="true" />
          </button>
        )}
      </div>
      <p id="composer-help" className="composer__help">
        Enter to send · Shift+Enter for a new line
      </p>
      <p
        id="composer-status"
        className="composer__status"
        role="status"
        aria-live="polite"
      >
        {submissionStatus === "submitting"
          ? "Roamora is planning. You can cancel waiting."
          : submissionStatus === "cancelled"
            ? "You stopped waiting. Backend work may still continue."
            : "This conversation is kept only in memory and clears on refresh."}
      </p>
    </form>
  );
}
