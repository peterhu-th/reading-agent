const STAGES = [
  ["understanding", "理解问题"],
  ["retrieving", "检索原文"],
  ["checking", "检查证据"],
  ["supplementing", "补充检索"],
  ["answering", "组织回答"],
] as const;

export function StatusStrip({ stage, message }: { stage: string; message: string }) {
  const activeIndex = STAGES.findIndex(([key]) => key === stage);
  return (
    <div className="status-strip" aria-live="polite">
      <div className="status-track">
        {STAGES.map(([key, label], index) => (
          <div className={`status-step ${index <= activeIndex ? "is-active" : ""}`} key={key}>
            <span aria-hidden="true" />
            <small>{label}</small>
          </div>
        ))}
      </div>
      <p>{message}</p>
    </div>
  );
}
