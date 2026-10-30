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
        approval_required: "待审批",
        prepared: "待审批",
        approved: "已审批",
        queued: "待运行",
        starting: "启动中",
        running: "运行中",
        verifying: "独立验证中",
        reviewing: "待人工审查",
        rework: "返工中",
        blocked: "已拦截",
        accepted: "已验收",
        completed: "已完成",
        cancelled: "已取消",
        timed_out: "已超时",
        failed: "失败",
        escalated: "需协调",
        needs_reconciliation: "需人工对账",
      } as Record<string, string>
    )[state] || state
  );
}
export function stateTone(state: string): string {
  if (["accepted", "completed", "approved"].includes(state)) return "success";
  if (
    [
      "reviewing",
      "approval_required",
      "prepared",
      "rework",
      "blocked",
      "escalated",
      "needs_reconciliation",
    ].includes(state)
  )
    return "warn";
  if (["failed", "timed_out", "cancelled"].includes(state)) return "danger";
  if (["running", "verifying", "starting", "queued"].includes(state))
    return "info";
  return "";
}
export function providerBadge(provider: string): {
  title: string;
  subtitle: string;
  accent: string;
  svg: string;
} {
  const map: Record<
    string,
    { title: string; subtitle: string; accent: string; svg: string }
  > = {
    mock: {
      title: "Mock 仿真引擎",
      subtitle: "离线确定性执行器 · 零模型消耗",
      accent: "accent-slate",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="4"/><path d="M9 9h6v6H9z"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>`,
    },
    codex: {
      title: "OpenAI Codex CLI",
      subtitle: "原生 CLI · 复用 ChatGPT 登录态",
      accent: "accent-indigo",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 21 7 21 17 12 22 3 17 3 7 12 2"/><path d="M12 22V12"/><path d="M21 7l-9 5-9-5"/></svg>`,
    },
    claude: {
      title: "Claude Code CLI",
      subtitle: "原生 CLI · 独立账本强约束保护",
      accent: "accent-amber",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6L5.6 18.4"/></svg>`,
    },
    "qoder-cn": {
      title: "Qoder CN",
      subtitle: "原生 CLI 1.1.80 · 支持 1M 上下文与高强度思考",
      accent: "accent-blue",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M16 16l4.5 4.5"/><path d="M8.5 11h5M11 8.5v5"/></svg>`,
    },
    deepseek: {
      title: "DeepSeek Harness",
      subtitle: "深度求索本地 CLI · 支持 Reasoner 深度思考",
      accent: "accent-cyan",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12c2-4 6-7 10-7s8 3 10 7c-2 4-6 7-10 7s-8-3-10-7z"/><circle cx="12" cy="12" r="3"/></svg>`,
    },
    antigravity: {
      title: "Gemini / Antigravity CLI",
      subtitle: "Google Antigravity & Gemini 本地 CLI 代理",
      accent: "accent-purple",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L14.8 9.2L22 12L14.8 14.8L12 22L9.2 14.8L2 12L9.2 9.2L12 2Z"/></svg>`,
    },
  };
  return (
    map[provider] || {
      title: provider,
      subtitle: "原生 CLI · 复用本机认证",
      accent: "accent-blue",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/></svg>`,
    }
  );
}
