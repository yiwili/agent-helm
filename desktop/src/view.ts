export function escape(value: unknown): string {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
}
export function stateLabel(state: string): string {
  return (
    (
      {
        prepared: "待审批",
        approved: "已审批",
        queued: "待运行",
        running: "运行中",
        reviewing: "待人工审查",
        accepted: "已验收",
        completed: "已完成",
        cancelled: "已取消",
        failed: "失败",
        escalated: "需协调",
      } as Record<string, string>
    )[state] || state
  );
}
