export function AmbientBackground() {
  return (
    <div className="ambient-background" aria-hidden="true">
      <span className="ambient-background__sun" />
      <span className="ambient-background__ridge ambient-background__ridge--far" />
      <span className="ambient-background__ridge ambient-background__ridge--near" />
      <span className="ambient-background__glow" />
      <span className="ambient-background__grain" />
    </div>
  );
}
