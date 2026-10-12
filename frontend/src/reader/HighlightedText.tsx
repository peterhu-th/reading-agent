export function HighlightedText({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return <>{text.split(new RegExp(`(${escaped})`, "giu")).map((part, index) =>
    index % 2 ? <mark key={index}>{part}</mark> : part,
  )}</>;
}
