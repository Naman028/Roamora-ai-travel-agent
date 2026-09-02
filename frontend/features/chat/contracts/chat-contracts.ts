import { z } from "zod";
import { modelVariantSchema } from "@/features/model-selection/contracts/model-variant";

export const CHAT_MESSAGE_MIN_LENGTH = 1;
export const CHAT_MESSAGE_MAX_LENGTH = 4000;
export const CHAT_MIN_NEW_TOKENS = 384;
export const CHAT_MAX_NEW_TOKENS = 1800;
export const CHAT_REQUEST_MAX_BYTES = 8 * 1024;
export const CHAT_RESPONSE_MAX_BYTES = 512 * 1024;
export const BACKEND_GENERATION_TIMEOUT_MS = 120_000;
export const BFF_CHAT_TIMEOUT_MS = 125_000;

export const chatRequestSchema = z
  .object({
    message: z
      .string()
      .trim()
      .min(CHAT_MESSAGE_MIN_LENGTH)
      .max(CHAT_MESSAGE_MAX_LENGTH),
    model_variant: modelVariantSchema,
    max_new_tokens: z
      .number()
      .int()
      .min(CHAT_MIN_NEW_TOKENS)
      .max(CHAT_MAX_NEW_TOKENS)
      .optional(),
  })
  .strict();

const optionalText = z.string().nullable().optional();
const optionalNumber = z.number().nullable().optional();
const moneyValue = z.union([z.number(), z.string(), z.null()]).optional();

const tripSummarySchema = z
  .object({
    destination: optionalText,
    duration_days: z.number().int().nullable().optional(),
    travelers: z.union([z.number(), z.string(), z.null()]).optional(),
    budget: moneyValue,
    origin: optionalText,
    currency: optionalText,
    source: optionalText,
  })
  .strict();

const weatherResultSchema = z
  .object({
    location: optionalText,
    city: optionalText,
    temperature: optionalNumber,
    temperature_c: optionalNumber,
    description: optionalText,
    condition: optionalText,
    humidity: z.number().int().nullable().optional(),
    wind_speed: optionalNumber,
  })
  .strict();

const flightResultSchema = z
  .object({
    origin: optionalText,
    destination: optionalText,
    departure_date: optionalText,
    return_date: optionalText,
    price: moneyValue,
    currency: optionalText,
    airline: optionalText,
    flight_number: optionalText,
    source: optionalText,
  })
  .strict();

const hotelResultSchema = z
  .object({
    name: optionalText,
    location: optionalText,
    price_per_night: moneyValue,
    price_total: moneyValue,
    price_range: optionalText,
    currency: optionalText,
    pricing_basis: z
      .enum(["per_night", "total_stay"])
      .nullable()
      .optional(),
    source: optionalText,
    rating: optionalNumber,
    amenities: z.array(z.string()).optional(),
  })
  .strict();

const eventResultSchema = z
  .object({
    name: optionalText,
    date: optionalText,
    time: optionalText,
    location: optionalText,
    venue: optionalText,
    city: optionalText,
    country: optionalText,
    category: optionalText,
    price: moneyValue,
    url: optionalText,
  })
  .strict();

const foodRecommendationSchema = z
  .object({
    name: z.string(),
    type: optionalText,
    price_range: optionalText,
    source: optionalText,
    rating: optionalNumber,
  })
  .strict();

const itineraryItemSchema = z
  .object({
    day: z.number().int(),
    time: optionalText,
    activity: z.string(),
    location: optionalText,
    budget_eur: z.number().nullable().optional(),
    source: optionalText,
  })
  .strict();

const mapMarkerSchema = z
  .object({
    label: z.string(),
    type: z.string().optional(),
    lat: optionalNumber,
    lng: optionalNumber,
  })
  .strict();

const routeSegmentSchema = z
  .object({
    origin: optionalText,
    destination: optionalText,
    transport: optionalText,
    distance: optionalText,
  })
  .strict();

const mapDataSchema = z
  .object({
    markers: z.array(mapMarkerSchema).optional(),
    route_segments: z
      .array(z.union([routeSegmentSchema, z.string()]))
      .optional(),
  })
  .strict();

const costDetailSchema = z
  .object({
    amount: z.number().optional(),
    currency: z.string().optional(),
    source: z.string().optional(),
    confidence: z.string().optional(),
    verified: z.boolean().optional(),
    pricing_basis: optionalText,
  })
  .strict();

const costDetailsSchema = z
  .object({
    transport: costDetailSchema.optional(),
    accommodation: costDetailSchema.optional(),
    food: costDetailSchema.optional(),
    activities: costDetailSchema.optional(),
    events: costDetailSchema.optional(),
  })
  .strict();

const budgetBreakdownSchema = z
  .object({
    currency: optionalText,
    transport: moneyValue,
    food: moneyValue,
    activities: moneyValue,
    accommodation: moneyValue,
    intercity_transport: moneyValue,
    total_known_cost: moneyValue,
    total_estimated_cost: moneyValue,
    model_estimated_total: moneyValue,
    total: moneyValue,
    remaining_budget: moneyValue,
    remaining_budget_before_transport_and_accommodation: moneyValue,
    within_budget: z.boolean().nullable().optional(),
    cost_details: costDetailsSchema.nullable().optional(),
    calculation_warnings: z.array(z.string()).optional(),
    note: optionalText,
    source: optionalText,
  })
  .strict();

const apiGroundingSchema = z
  .object({
    used_api: z.array(z.string()).optional(),
    missing_api: z.array(z.string()).optional(),
    warnings: z.array(z.string()).optional(),
  })
  .strict();

const weatherSectionSchema = z
  .object({
    data: weatherResultSchema.nullable().optional(),
    source: z.string().optional(),
    status: z.string().optional(),
  })
  .strict();

const flightSectionSchema = z
  .object({
    data: z.array(flightResultSchema).optional(),
    source: z.string().optional(),
    status: z.string().optional(),
  })
  .strict();

const hotelSectionSchema = z
  .object({
    data: z.array(hotelResultSchema).optional(),
    source: z.string().optional(),
    status: z.string().optional(),
  })
  .strict();

const eventSectionSchema = z
  .object({
    data: z.array(eventResultSchema).optional(),
    source: z.string().optional(),
    status: z.string().optional(),
  })
  .strict();

export const dashboardPayloadSchema = z
  .object({
    schema_version: z.literal("travel_dashboard_v1").optional(),
    intent: z.string(),
    trip_summary: tripSummarySchema.optional(),
    weather: weatherSectionSchema.nullable().optional(),
    flights: flightSectionSchema.optional(),
    hotels: hotelSectionSchema.optional(),
    local_events: eventSectionSchema.optional(),
    food_recommendations: z.array(foodRecommendationSchema).optional(),
    itinerary: z.array(itineraryItemSchema).optional(),
    map_data: mapDataSchema.optional(),
    budget_breakdown: budgetBreakdownSchema.optional(),
    dashboard_actions: z.array(z.string()).optional(),
    api_grounding: apiGroundingSchema.optional(),
    itinerary_source: optionalText,
    assistant_message_source: optionalText,
  })
  .strict();

export const chatResponseSchema = z
  .object({
    session_id: z.string(),
    selected_model: modelVariantSchema,
    adapter_loaded: z.boolean(),
    parse_success: z.boolean(),
    fallback_used: z.boolean(),
    retry_used: z.boolean(),
    assistant_message: z.string(),
    dashboard_payload: dashboardPayloadSchema,
  })
  .strict();

export const fieldErrorSchema = z
  .object({
    field: z.string(),
    message: z.string(),
  })
  .strict();

export const backendErrorResponseSchema = z
  .object({
    error: z
      .object({
        code: z.string(),
        message: z.string(),
        request_id: z.string(),
        retryable: z.boolean().optional(),
        field_errors: z.array(fieldErrorSchema).optional(),
      })
      .strict(),
  })
  .strict();

export const chatErrorResponseSchema = z
  .object({
    error: z
      .object({
        code: z.string(),
        message: z.string(),
        requestId: z.string(),
        retryable: z.boolean(),
        fieldErrors: z.array(fieldErrorSchema),
      })
      .strict(),
  })
  .strict();

export type ChatRequest = z.infer<typeof chatRequestSchema>;
export type ChatResponse = z.infer<typeof chatResponseSchema>;
export type ChatErrorResponse = z.infer<typeof chatErrorResponseSchema>;
