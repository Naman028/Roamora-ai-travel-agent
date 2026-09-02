import {
  modelVariantSchema,
  type ModelVariant,
} from "@/features/model-selection/contracts/model-variant";

const MODEL_DESCRIPTIONS: Record<ModelVariant, string> = {
  fine_tuned: "Roamora travel-tuned model",
  base: "Original model for comparison",
};

export function ModelSelector({
  value,
  onChange,
}: {
  value: ModelVariant;
  onChange: (value: ModelVariant) => void;
}) {
  const descriptionId = "model-selector-description";
  const selectorId = "model-selector";

  return (
    <div className="model-selector">
      <label className="model-selector__label" htmlFor={selectorId}>
        Model
      </label>
      <select
        id={selectorId}
        value={value}
        aria-describedby={descriptionId}
        onChange={(event) => {
          const parsed = modelVariantSchema.safeParse(event.target.value);
          if (parsed.success) {
            onChange(parsed.data);
          }
        }}
      >
        <option value="fine_tuned">Fine-tuned</option>
        <option value="base">Base</option>
      </select>
      <span id={descriptionId} className="model-selector__description">
        {MODEL_DESCRIPTIONS[value]}
      </span>
    </div>
  );
}
