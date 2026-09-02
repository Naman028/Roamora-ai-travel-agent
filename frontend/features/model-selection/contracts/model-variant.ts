import { z } from "zod";

export const MODEL_VARIANTS = ["base", "fine_tuned"] as const;

export const modelVariantSchema = z.enum(MODEL_VARIANTS);

export type ModelVariant = z.infer<typeof modelVariantSchema>;

export const DEFAULT_MODEL_VARIANT: ModelVariant = "fine_tuned";
