export type AppPanel = "chat" | "current-trip" | "saved-trips";

export interface EmptyTripState {
  kind: "empty";
  title: string;
  description: string;
}

export interface SavedTripsState {
  kind: "empty";
  trips: readonly [];
}

export interface NormalizedFrontendError {
  status: number | null;
  code: string;
  message: string;
  retryable: boolean;
}
