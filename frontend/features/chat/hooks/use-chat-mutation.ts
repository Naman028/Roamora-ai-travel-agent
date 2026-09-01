"use client";

import { useMutation } from "@tanstack/react-query";
import { sendChatMessage } from "@/features/chat/api/send-chat-message";
import type { SendChatVariables } from "@/features/chat/types/chat.types";

export function useChatMutation() {
  return useMutation({
    mutationFn: ({ input, signal }: SendChatVariables) =>
      sendChatMessage(input, signal),
    retry: false,
  });
}
