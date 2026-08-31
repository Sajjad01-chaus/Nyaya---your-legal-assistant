export default function Confidence({ level, score }: { level: string; score?: number }) {
  const known = ["high", "medium", "low"].includes(level) ? level : "";
  return (
    <span className={`badge ${known}`}>
      {level}
      {score !== undefined ? ` ${score.toFixed(2)}` : ""}
    </span>
  );
}
