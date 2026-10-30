import "./style.css";
import { connected, request } from "./bridge";
import { escape as e, stateLabel } from "./view";

type Agent = {
  provider: string;
  name: string;
  enabled: boolean;
  connection: string;
  executable: string;
  model: string;
  models: string[];
  budget_ledger: string;
};
type Dashboard = {
  agents: Agent[];
  runs: any[];
  probes: Record<string, any>;
  active_runs: number;
  state_root: string;
};
let data: Dashboard = {
  agents: [],
  runs: [],
  probes: {},
  active_runs: 0,
  state_root: "",
};
let tab = "tasks",
  busy = false,
  detail: any = null,
  proposal: any = null,
  error = "",
  notice = "";
let taskDraftDirty = false;
const app = document.querySelector<HTMLDivElement>("#app")!;
const json = (value: unknown) => e(JSON.stringify(value, null, 2));
const button = (label: string, action: string, attrs = "") =>
  `<button data-action="${action}" ${attrs} ${busy || !connected ? "disabled" : ""}>${label}</button>`;
const labels: Record<string, string> = {
  tasks: "任务工作台",
  agents: "子 Agent",
  settings: "连接与预算",
};

function render() {
  app.innerHTML = `<aside><a class="brand" href="#"><span class="logo">H</span> Agent Helm <small>LOCAL STUDIO</small></a><nav>${Object.entries(
    labels,
  )
    .map(
      ([id, label]) =>
        `<button data-tab="${id}" class="${tab === id ? "selected" : ""}"><span>${id === "tasks" ? "◈" : id === "agents" ? "◎" : "⚙"}</span>${label}</button>`,
    )
    .join(
      "",
    )}</nav><div class="aside-note"><i></i> 本地优先<br><small>独立证据 · 人工审批</small></div></aside>
 <main><header><div><p class="eyebrow">WORKSPACE / SYNTHETIC LAB</p><h1>${labels[tab]}</h1></div><span class="pill ${connected ? "" : "warn"}">${connected ? "桌面服务" : "浏览器预览 · 未连接"}</span></header>
 ${error ? `<div role="alert" class="alert">${e(error)}</div>` : ""}${notice ? `<div role="status" class="notice">${e(notice)}</div>` : ""}
 <section class="scope"><span>◇</span><div><strong>独立合成实验空间</strong><p>仅生成并操作无敏感数据的示例项目；真实 CLI 默认关闭，配置更改用于后续新任务。</p></div></section>
 ${tab === "tasks" ? tasks() : tab === "agents" ? agents() : settings()}
 <footer>Agent Helm · Tauri 2 <span>Windows 开发版 / 不执行付费模型探测</span></footer></main>${proposal ? approval() : ""}`;
  bind();
}
function tasks() {
  return `<div class="stats"><article><small>本地任务</small><strong>${data.runs.length.toString().padStart(2, "0")}</strong></article><article><small>正在执行</small><strong>${data.active_runs.toString().padStart(2, "0")}</strong></article><article><small>可选子 Agent</small><strong>${data.agents
    .filter((a) => a.enabled)
    .length.toString()
    .padStart(2, "0")}</strong></article></div>
 <section class="panel"><div class="section-head"><div><h2>创建任务</h2><p>生成新的合成样例，审阅计划后再运行。</p></div><span class="pill">人工审批</span></div><form id="task-form"><label>任务目标<input name="goal" required maxlength="2000" value="修复合成样例中的空列表求和"></label><div class="row"><label>下游 Worker<select name="provider">${data.agents
   .filter((a) => a.enabled)
   .map(
     (a) =>
       `<option value="${e(a.provider)}">${e(a.name)} · ${e(a.model || "沿用 CLI 配置")}</option>`,
   )
   .join(
     "",
   )}</select></label><label class="check"><input type="checkbox" name="fault_demo">Mock 首轮注入故障，演示返工</label></div>${button("生成待审批计划 →", "create", 'type="submit"')}</form></section>
 <section class="panel"><div class="section-head"><h2>任务记录</h2>${button("刷新", "refresh")}</div>${data.runs.length ? `<div class="runs">${data.runs.map((r) => `<button class="run" data-action="${r.run_id ? "detail" : "proposal"}" data-id="${e(r.run_id || r.proposal_id)}"><span class="run-icon">↗</span><span><strong>${e(r.goal)}</strong><small>${e(r.provider)} · ${e((r.run_id || r.proposal_id).slice(0, 24))}</small></span><span class="pill">${e(stateLabel(r.state))}</span></button>`).join("")}</div>` : '<div class="empty">◇<h3>从一个小任务开始</h3><p>先用 Mock 走通审批、执行和独立验收，无需登录或模型额度。</p></div>'}</section>${detail ? details() : ""}`;
}
function agents() {
  return `<div class="section-head"><div><h2>选择执行者，保留你的模型配置</h2><p>沿用本机 CLI 登录与 CC Switch 设置；不收集密钥。模型 ID 必须由对应 CLI 支持。</p></div></div><div class="agent-grid">${data.agents.map((a) => `<form class="panel agent" data-provider="${a.provider}"><div class="section-head"><span class="agent-icon">${e(a.name[0])}</span><label class="check"><input type="checkbox" name="enabled" ${a.enabled ? "checked" : ""} ${a.provider === "mock" ? "disabled" : ""}>启用</label></div><h2>${e(a.name)}</h2><p>${a.provider === "mock" ? "离线执行器 · 零模型调用" : "原生 CLI · 复用本机认证"}</p><label>Worker 模型<select name="model" ${a.provider === "mock" ? "disabled" : ""}><option value="">沿用 CLI / CC Switch 当前配置</option>${a.models.map((m) => `<option ${m === a.model ? "selected" : ""} value="${e(m)}">${e(m)}</option>`).join("")}</select></label>${a.provider === "mock" ? "" : `<label>添加或切换模型 ID<input name="custom_model" placeholder="输入准确模型 ID；留空使用上方选项" maxlength="200" autocomplete="off"></label><label>连接方式<select name="connection"><option value="auto" ${a.connection === "auto" ? "selected" : ""}>自动发现本机 CLI</option><option value="path" ${a.connection === "path" ? "selected" : ""}>指定本机原生 CLI 路径</option></select></label><label class="path-label" ${a.connection === "path" ? "" : "hidden"}>CLI 可执行文件绝对路径<input name="executable" value="${e(a.executable)}" placeholder="原生 .exe 或脚本入口；不填写 shell 命令" autocomplete="off"></label>`}<div class="actions">${button("保存配置", "save", 'type="submit"')}${button("检查连接", "probe", `type="button" data-id="${a.provider}"`)}</div>${data.probes[a.provider] ? `<pre class="probe">${json(data.probes[a.provider])}</pre>` : ""}</form>`).join("")}</div>`;
}
function settings() {
  return `<section class="panel"><h2>本地连接</h2><p>支持自动发现和指定原生 CLI 文件。连接探测只读取版本与认证状态，不启动模型推理。</p><dl><dt>运行状态目录</dt><dd>${e(data.state_root || "未连接")}</dd><dt>认证</dt><dd>由各 CLI 管理；不读取或复制凭据。</dd><dt>连接能力</dt><dd>Codex / Claude CLI / Qoder CN。当前不支持将任意 HTTP、MCP 地址当作 Worker。</dd></dl></section><section class="panel"><h2>Claude 独立预算</h2><p>每次推理前从本地账本预留。失败不自动退款；费用不确定时停止并人工核对。CLI 估算不能保证代理账单硬上限。</p><form id="budget-form"><div class="row"><label>本次新增总额度（USD）<input name="total" type="number" min="0.01" step="0.01" value="0.30" required></label><label>单次最高（USD）<input name="per_call" type="number" min="0.01" step="0.01" value="0.10" required></label></div><label class="check"><input name="confirmed" type="checkbox" required>我明确授权此新增额度；创建账本本身不会调用模型。</label><div class="actions">${button("确认创建新账本", "budget", 'type="submit"')}${button("读取账本", "budget-status", 'type="button"')}</div></form></section>`;
}
function approval() {
  return `<div class="overlay"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="approval-title"><p class="eyebrow">HUMAN APPROVAL</p><h2 id="approval-title">审阅计划与 Worker 配置</h2><p>确认后将启动该 Worker。真实 CLI 可能消耗订阅额度；Claude 还须已有授权账本。</p><pre>${json({ plan: proposal.plan, agent_configuration: proposal.agent_configuration, plan_hash: proposal.plan_hash, runtime_hash: proposal.runtime_hash })}</pre><form id="approve-form"><label class="check"><input type="checkbox" name="confirmed" required>我已审阅此计划、模型和连接配置，授权本次执行。</label><div class="actions">${button("确认并执行", "approve", 'type="submit"')}<button type="button" data-action="close">稍后</button></div></form></section></div>`;
}
function details() {
  const s = detail.status;
  const evidence = detail.evidence
    .map(
      (ev: any) =>
        `<article class="evidence-card"><h3>${e(ev.task_id)}</h3>${ev.attempts.map((a: any) => `<section><p>第 ${e(a.attempt)} 次尝试 · ${e(stateLabel(a.state))}</p><div class="evidence-gates"><span class="pill ${a.verification_passed ? "" : "warn"}">独立测试 ${a.verification_passed ? "通过" : "未通过"}</span><span class="pill ${a.scope_ok ? "" : "warn"}">修改范围 ${a.scope_ok ? "通过" : "未通过"}</span></div><pre>${e(a.diff || "尚无候选差异")}</pre><small>候选哈希：${e(a.candidate_hash || "尚未生成")}</small></section>`).join("")}</article>`,
    )
    .join("");
  return `<section class="panel" id="detail"><div class="section-head"><h2>执行证据 · ${e(stateLabel(s.state))}</h2>${button("更新证据", "detail", `data-id="${e(s.run_id || s.id)}"`)}</div><p>以下候选内容是 Worker 输出，请结合独立测试审阅。</p>${evidence || "<p>等待执行器生成候选和验证证据。</p>"}<details><summary>进度与审查记录</summary><pre>${json({ tasks: s.tasks, events: detail.events, reviews: detail.evidence.map((ev: any) => ({ task: ev.task_id, reviews: ev.reviews })) })}</pre></details><div class="actions">${button("请求取消", "cancel", `data-id="${e(s.run_id || s.id)}"`)}${s.state === "queued" ? button("确认继续返工", "resume", `data-id="${e(s.run_id || s.id)}"`) : ""}${s.state === "accepted" ? button("集成到合成样例", "integrate", `data-id="${e(s.run_id || s.id)}"`) : ""}</div>${
    s.state === "reviewing"
      ? detail.evidence
          .map((ev: any, i: number) => {
            const candidate = ev.attempts?.at(-1);
            return candidate
              ? `<form class="review-form" data-task="${e(s.tasks[i].id)}" data-hash="${e(candidate.candidate_hash)}"><h3>人工审查 ${e(s.tasks[i].id)}</h3><select name="decision"><option value="accept">接受（仍需独立测试与范围通过）</option><option value="rework">请求返工</option><option value="escalate">升级人工处理</option></select><label class="check"><input type="checkbox" name="confirmed" required>我已检查以上 diff 和验证证据。</label>${button("提交审查", "review", 'type="submit"')}</form>`
              : "";
          })
          .join("")
      : ""
  }</section>`;
}
async function perform(fn: () => Promise<void>) {
  if (busy) return;
  busy = true;
  error = "";
  notice = "";
  try {
    await fn();
  } catch (ex) {
    error = String(ex);
  } finally {
    busy = false;
    render();
  }
}
async function refresh() {
  data = await request<Dashboard>("dashboard");
}
function bind() {
  app.querySelector("#task-form")?.addEventListener("input", () => {
    taskDraftDirty = true;
  });
  app.querySelectorAll<HTMLElement>("[data-tab]").forEach(
    (el) =>
      (el.onclick = () => {
        tab = el.dataset.tab!;
        render();
      }),
  );
  app.querySelectorAll<HTMLSelectElement>("[name=connection]").forEach(
    (el) =>
      (el.onchange = () => {
        (
          el.closest("form")!.querySelector(".path-label") as HTMLElement
        ).hidden = el.value !== "path";
      }),
  );
  app.querySelectorAll<HTMLButtonElement>("[data-action]").forEach(
    (el) =>
      (el.onclick = () => {
        if (el.type === "submit" && el.form) return;
        const action = el.dataset.action!,
          id = el.dataset.id;
        if (action === "close") {
          proposal = null;
          render();
          return;
        }
        void perform(async () => {
          if (action === "refresh") await refresh();
          else if (action === "probe") {
            await request("probe_agent", { provider: id });
            await refresh();
          } else if (action === "proposal") {
            proposal = await request("proposal", { proposal_id: id });
          } else if (action === "detail") {
            detail = await request("run_detail", { run_id: id });
          } else if (action === "budget-status") {
            notice = JSON.stringify(await request("budget_status"));
          } else if (["cancel", "resume", "integrate"].includes(action)) {
            await request(action, { run_id: id, confirmed: true });
            await refresh();
            detail = await request("run_detail", { run_id: id });
          }
        });
      }),
  );
  app
    .querySelector<HTMLFormElement>("#task-form")
    ?.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.currentTarget as HTMLFormElement);
      void perform(async () => {
        const p = await request("create_task", {
          goal: f.get("goal"),
          provider: f.get("provider"),
          fault_demo: f.has("fault_demo"),
        });
        taskDraftDirty = false;
        proposal = await request("proposal", { proposal_id: p.proposal_id });
        await refresh();
      });
    });
  app.querySelectorAll<HTMLFormElement>("form.agent").forEach(
    (form) =>
      (form.onsubmit = (ev) => {
        ev.preventDefault();
        const f = new FormData(form),
          old = data.agents.find((a) => a.provider === form.dataset.provider)!;
        void perform(async () => {
          await request("save_agent", {
            profile: {
              ...old,
              enabled: old.provider === "mock" || f.has("enabled"),
              model: String(
                f.get("custom_model") || f.get("model") || "",
              ).trim(),
              connection: f.get("connection") || "auto",
              executable: String(f.get("executable") || "").trim(),
            },
          });
          await refresh();
          notice = "配置已保存，将用于之后创建的任务。";
        });
      }),
  );
  app
    .querySelector<HTMLFormElement>("#approve-form")
    ?.addEventListener("submit", (ev) => {
      ev.preventDefault();
      void perform(async () => {
        const r = await request("approve_start", {
          proposal_id: proposal.proposal_id,
          plan_hash: proposal.plan_hash,
          runtime_hash: proposal.runtime_hash,
          confirmed: true,
        });
        proposal = null;
        await refresh();
        detail = await request("run_detail", { run_id: r.run_id });
      });
    });
  app
    .querySelector<HTMLFormElement>("#budget-form")
    ?.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.currentTarget as HTMLFormElement);
      void perform(async () => {
        notice = JSON.stringify(
          await request("budget_create", {
            total_usd: Number(f.get("total")),
            per_call_usd: Number(f.get("per_call")),
            confirmed: f.has("confirmed"),
          }),
        );
        await refresh();
      });
    });
  app.querySelectorAll<HTMLFormElement>(".review-form").forEach(
    (form) =>
      (form.onsubmit = (ev) => {
        ev.preventDefault();
        const f = new FormData(form);
        void perform(async () => {
          await request("review", {
            run_id: detail.status.run_id || detail.status.id,
            task_id: form.dataset.task,
            candidate_hash: form.dataset.hash,
            decision: f.get("decision"),
            confirmed: f.has("confirmed"),
          });
          await refresh();
          detail = await request("run_detail", {
            run_id: detail.status.run_id || detail.status.id,
          });
        });
      }),
  );
}
render();
if (connected) {
  void perform(refresh);
  setInterval(() => {
    if (
      tab === "tasks" &&
      !taskDraftDirty &&
      !busy &&
      !proposal &&
      !app.querySelector("input:focus,select:focus")
    )
      void perform(async () => {
        await refresh();
        if (detail)
          detail = await request("run_detail", {
            run_id: detail.status.run_id || detail.status.id,
          });
      });
  }, 4000);
}
