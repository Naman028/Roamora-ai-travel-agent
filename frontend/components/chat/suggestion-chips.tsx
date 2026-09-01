const suggestions = [
  "Plan a slow weekend",
  "Explore by train",
  "Find a coastal escape",
] as const;

export function SuggestionChips({
  onSelect,
  disabled = false,
}: {
  onSelect: (suggestion: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="suggestions" aria-label="Prompt suggestions">
      {suggestions.map((suggestion) => (
        <button
          key={suggestion}
          type="button"
          className="suggestion-chip"
          onClick={() => onSelect(suggestion)}
          disabled={disabled}
        >
          {suggestion}
        </button>
      ))}
    </div>
  );
}
