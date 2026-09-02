import { describe, expect, it } from "vitest";
import {
  DEFAULT_MODEL_VARIANT,
  MODEL_VARIANTS,
  modelVariantSchema,
} from "@/features/model-selection/contracts/model-variant";
import { chatRequestSchema } from "@/features/chat/contracts/chat-contracts";

describe("canonical model variant contract", () => {
  it("accepts only the exact base and fine_tuned values", () => {
    expect(MODEL_VARIANTS).toEqual(["base", "fine_tuned"]);
    expect(modelVariantSchema.parse("base")).toBe("base");
    expect(modelVariantSchema.parse("fine_tuned")).toBe("fine_tuned");
  });

  it.each(["other", "Base", "BASE", "fine-tuned", "finetuned", "Fine_Tuned"])(
    "rejects %s without normalization",
    (value) => {
      expect(modelVariantSchema.safeParse(value).success).toBe(false);
    },
  );

  it("preserves the verified backend default while requiring browser intent", () => {
    expect(DEFAULT_MODEL_VARIANT).toBe("fine_tuned");
    expect(
      chatRequestSchema.safeParse({ message: "missing selection" }).success,
    ).toBe(false);
  });
});
