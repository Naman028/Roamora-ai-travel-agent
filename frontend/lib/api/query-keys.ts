export const queryKeys = {
  all: ["roamora"] as const,
  currentUser: () => [...queryKeys.all, "current-user"] as const,
  conversations: () => [...queryKeys.all, "conversations"] as const,
  conversation: (conversationId: string) =>
    [...queryKeys.conversations(), conversationId] as const,
  messages: (conversationId: string) =>
    [...queryKeys.conversation(conversationId), "messages"] as const,
  trips: () => [...queryKeys.all, "trips"] as const,
  trip: (tripId: string) => [...queryKeys.trips(), tripId] as const,
  currentTrip: () => [...queryKeys.trips(), "current"] as const,
  savedTrips: () => [...queryKeys.trips(), "saved"] as const,
};
