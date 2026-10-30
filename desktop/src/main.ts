import "./style.css";
import { connected, request } from "./bridge";
import { escape as e, providerBadge, stateLabel, stateTone } from "./view";

type Agent = {
  provider: string;
  name: string;
  enabled: boolean;
  connection: string;
  executable: string;
  model: string;
  models: string[];
  preset_models?: string[];
  reasoning_effort?: string;
  thinking?: string;
  context_window?: string;
  review_mode?: string;
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
let orchMode = "direct";
const app = document.querySelector<HTMLDivElement>("#app")!;
const json = (value: unknown) => e(JSON.stringify(value, null, 2));
const button = (label: string, action: string, attrs = "") =>
  `<button data-action="${action}" ${attrs} ${busy || !connected ? "disabled" : ""}>${label}</button>`;
const labels: Record<string, string> = {
  tasks: "任务编排台",
  agents: "下游 Agent",
  settings: "协议与预算",
};
const navIcons: Record<string, string> = {
  tasks: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/></svg>`,
  agents: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a4 4 0 0 1 4 4v2a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4z"/><path d="M16 14h2a4 4 0 0 1 4 4v2H2v-2a4 4 0 0 1 4-4h2"/><circle cx="12" cy="17" r="2"/></svg>`,
  settings: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.6.77 1.05 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
};

function render() {
  app.innerHTML = `<aside><div class="window-controls" aria-hidden="true"><span class="dot red"></span><span class="dot yellow"></span><span class="dot green"></span></div><a class="brand" href="#"><span class="logo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6M3 12h6M15 12h6"/></svg></span><div><strong>Agent Helm</strong><small>APPLE STUDIO EDITION</small></div></a><nav>${Object.entries(
    labels,
  )
    .map(
      ([id, label]) =>
        `<button data-tab="${id}" class="${tab === id ? "selected" : ""}"><span class="nav-icon">${navIcons[id]}</span>${label}</button>`,
    )
    .join(
      "",
    )}</nav><div class="aside-note"><i></i><div><strong>Leader-Worker 协同</strong><small>AHCP-1 省流协议 · 中文总结</small></div></div></aside>
 <main><header><div><p class="eyebrow">MULTI-AGENT LEADER-WORKER STUDIO</p><h1>${labels[tab]}</h1></div><span class="pill ${connected ? "success" : "warn"}">${connected ? "桌面核心已连接" : "浏览器预览 · 未连接"}</span></header>
 ${error ? `<div role="alert" class="alert">${e(error)}</div>` : ""}${notice ? `<div role="status" class="notice">${e(notice)}</div>` : ""}
 <section class="scope"><span class="scope-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg></span><div><strong>支持自定义 Leader 编排 · 多角色拆解/原话直通 · AHCP-1 极简英文通信协议</strong><p>输入提示词 100% 完整保留以确保理解准确；Agent 间通信与返回采用 AHCP-1 紧凑英文键（<code>s/f/p/c</code> 与 <code>d/h/r/e</code>）大幅节省输出 Token，并由模板引擎自动填充与生成中文总结。</p></div></section>
 ${tab === "tasks" ? tasks() : tab === "agents" ? agents() : settings()}
 <footer><span>Agent Helm · macOS Design System · AHCP-1 Wire Protocol</span><span>Windows 原生沙箱 / 不执行隐藏付费探测</span></footer></main>${proposal ? approval() : ""}`;
  bind();
}

function formatAgentOption(a: Agent): string {
  const modelDesc = a.model || "沿用 CLI 默认模型";
  const ctxDesc = a.context_window ? ` · ${a.context_window.toUpperCase()}` : "";
  const effortDesc = a.reasoning_effort
    ? ` · 思考:${a.reasoning_effort === "high" ? "高" : a.reasoning_effort === "max" ? "最大" : "低"}`
    : "";
  return `<option value="${e(a.provider)}">${e(a.name)} (${e(modelDesc)}${e(ctxDesc)}${e(effortDesc)})</option>`;
}

function tasks() {
  const enabledAgents = data.agents.filter((a) => a.enabled);
  const agentOpts = enabledAgents.map(formatAgentOption).join("");
  return `<div class="stats"><article><small>累计编排任务</small><strong>${data.runs.length.toString().padStart(2, "0")}</strong></article><article><small>正在并发执行</small><strong>${data.active_runs.toString().padStart(2, "0")}</strong></article><article><small>已就绪 Agent</small><strong>${enabledAgents.length.toString().padStart(2, "0")}</strong></article></div>
 <section class="panel"><div class="section-head"><div><h2>Leader-Worker 协同任务编排</h2><p>支持「模式一：Leader 直派单 Worker 闭环」与「模式二：Leader 任务拆解 + 多角色分配（不可拆解时自动原话直通上游 Agent）」。</p></div><span class="pill info">AHCP-1 省 Token 协议</span></div>
 <form id="task-form">
   <label>任务目标（支持使用分号 <code>；</code> 或换行书写多阶段目标供模式二拆解；若为单原子任务将原话直通）
     <input name="goal" required maxlength="2000" value="修复合成样例中的空列表求和">
   </label>
   <div class="row compact-row">
     <label>编排模式 (Orchestration Mode)
       <select name="mode" id="orch-mode-select">
         <option value="direct" ${orchMode === "direct" ? "selected" : ""}>模式一：Leader -> 指定 Worker -> Leader 审查 (支持返工闭环)</option>
         <option value="decompose" ${orchMode === "decompose" ? "selected" : ""}>模式二：Leader 拆解任务 -> 多角色并发/协同 -> (次级 Reviewer) -> Leader 终审</option>
       </select>
     </label>
     <label>指定 Leader (发起者 / 终审 Brain)
       <select name="leader">
         <option value="mock">内置确定性 Leader (零 Token 消耗 · 独立测试驱动)</option>
         ${enabledAgents.filter((a) => a.provider !== "mock").map((a) => `<option value="${e(a.provider)}">${e(a.name)} 作为 Leader (${e(a.model || "默认模型")})</option>`).join("")}
         <option value="interactive">人工 Leader (暂停等待人工审查裁决)</option>
       </select>
     </label>
   </div>
   <div class="row compact-row">
     <label>主力 / 默认上游 Worker（模式一执行者，或模式二无法拆解时的原话直通 Agent）
       <select name="provider">${agentOpts}</select>
     </label>
     <label>次级 Reviewer (Sub-Reviewer，可选)
       <select name="sub_reviewer">
         <option value="">不设置次级 Reviewer（直接交由 Leader 审查）</option>
         ${agentOpts}
       </select>
     </label>
   </div>
   <div class="role-matrix" ${orchMode === "decompose" ? "" : "hidden"}>
     <div class="section-head" style="margin-bottom:8px">
       <div><strong>模式二：角色分配矩阵 (Role Assignments)</strong><p>当任务可拆解时，Leader 将各阶段目标分别派发给以下角色；若任务不可拆解，Leader 将给出说明并把原话直接传给角色 1。</p></div>
     </div>
     <div class="row compact-row">
       <label>角色 1 名称<input name="role_1_name" value="implementer" placeholder="如 implementer / 编码实现"></label>
       <label>角色 1 执行 Agent<select name="role_1_worker">${agentOpts}</select></label>
       <label class="check" style="margin-top:18px"><input type="checkbox" name="role_1_parallel">允许并发</label>
     </div>
     <div class="row compact-row">
       <label>角色 2 名称<input name="role_2_name" value="verifier-guard" placeholder="如 verifier-guard / 边界复核"></label>
       <label>角色 2 执行 Agent<select name="role_2_worker">${agentOpts}</select></label>
       <label class="check" style="margin-top:18px"><input type="checkbox" name="role_2_parallel">允许并发 (无前置依赖)</label>
     </div>
   </div>
   <div class="row">
     <label class="check"><input type="checkbox" name="fault_demo">Mock 首轮注入故障，演示 Leader 打回通知 Worker 返工修正闭环</label>
   </div>
   ${button("生成待审批计划 →", "create", 'type="submit" class="btn-primary"')}
 </form></section>
 <section class="panel"><div class="section-head"><div><h2>运行记录与中文汇总结论</h2><p>点击任一记录查看 Leader 拆解说明、AHCP-1 通信帧、代码 Diff 及中文总结报告。</p></div>${button("刷新列表", "refresh")}</div>${
   data.runs.length
     ? `<div class="runs">${data.runs
         .map((r) => {
           const badge = providerBadge(r.provider);
           const modeBadge =
             r.mode === "decompose"
               ? r.splittable
                 ? "模式二·多角色拆解"
                 : "模式二·原话直通"
               : "模式一·直派闭环";
           return `<button class="run" data-action="${r.run_id ? "detail" : "proposal"}" data-id="${e(r.run_id || r.proposal_id)}"><span class="run-icon ${badge.accent}">${badge.svg}</span><span><strong>${e(r.goal)}</strong><small>Leader: ${e(r.leader || "mock")} · Worker: ${e(badge.title)} · ${modeBadge} · ${e((r.run_id || r.proposal_id).slice(0, 24))}</small></span><span class="pill ${stateTone(r.state)}">${e(stateLabel(r.state))}</span></button>`;
         })
         .join("")}</div>`
     : '<div class="empty"><div class="empty-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 10h8M8 14h5"/></svg></div><h3>尚无任务记录</h3><p>您可以选择模式一或模式二，指定 Leader 与下游 Worker 启动协同任务。</p></div>'
 }</section>${detail ? details() : ""}`;
}

function modelOptions(a: Agent): string[] {
  const presets = a.preset_models || [];
  const history = a.models || [];
  const merged: string[] = [];
  for (const item of [...history, ...presets]) {
    if (item && !merged.includes(item)) merged.push(item);
  }
  return merged;
}

function probeCard(probe: any): string {
  if (!probe) return "";
  const ok = probe.installed && probe.authenticated && probe.compatible !== false;
  return `<div class="probe-card ${ok ? "probe-ok" : "probe-warn"}">
    <div class="probe-badges">
      <span class="pill ${probe.installed ? "success" : "danger"}">${probe.installed ? "CLI 已安装" : "未检测到 CLI"}</span>
      <span class="pill ${probe.authenticated ? "success" : "warn"}">${probe.authenticated ? "已登录认证" : "未登录 / 待认证"}</span>
      ${probe.version ? `<span class="pill info">v${e(probe.version)}</span>` : ""}
    </div>
    ${probe.binary ? `<small class="probe-path">${e(probe.binary)}</small>` : ""}
    <pre class="probe">${json(probe)}</pre>
  </div>`;
}

function agents() {
  return `<div class="section-head"><div><h2>配置下游 Worker 与 Leader 代理参数</h2><p>支持自动发现本机 CLI 或直接粘贴可执行文件路径；切换模型、上下文窗口或思考强度后点击「保存配置」或「检查连接」即可自动生效。</p></div></div><div class="agent-grid">${data.agents
    .map((a) => {
      const badge = providerBadge(a.provider);
      const options = modelOptions(a);
      const effort = a.reasoning_effort || "";
      const ctx = a.context_window || "";
      const reviewMode = a.review_mode || "auto";
      return `<form class="panel agent" data-provider="${e(a.provider)}"><div class="section-head"><div class="agent-head-left"><span class="agent-icon ${badge.accent}">${badge.svg}</span><div><h2>${e(a.name)}</h2><p class="agent-sub">${e(badge.subtitle)}</p></div></div><label class="check switch-label"><input type="checkbox" name="enabled" ${a.enabled ? "checked" : ""} ${a.provider === "mock" ? "disabled" : ""}><span>启用</span></label></div><label>预设与历史 Worker 模型<select name="model" ${a.provider === "mock" ? "disabled" : ""}><option value="">沿用 CLI / CC Switch 当前配置</option>${options.map((m) => `<option ${m === a.model ? "selected" : ""} value="${e(m)}">${e(m)}</option>`).join("")}</select></label>${
        a.provider === "mock"
          ? ""
          : `<label>自定义或切换模型 ID<input name="custom_model" placeholder="如 deepseek-flash；留空则使用上方下拉所选模型" maxlength="200" autocomplete="off"></label>
          <div class="row compact-row">
            <label>上下文窗口 (Context)<select name="context_window">
              <option value="" ${ctx === "" ? "selected" : ""}>默认窗口</option>
              <option value="1m" ${ctx === "1m" ? "selected" : ""}>1M (1,000,000 tokens)</option>
              <option value="200k" ${ctx === "200k" ? "selected" : ""}>200K tokens</option>
              <option value="128k" ${ctx === "128k" ? "selected" : ""}>128K tokens</option>
            </select></label>
            <label>思考强度 (Thinking)<select name="reasoning_effort">
              <option value="" ${effort === "" ? "selected" : ""}>CLI 默认</option>
              <option value="low" ${effort === "low" ? "selected" : ""}>低 (Low)</option>
              <option value="high" ${effort === "high" ? "selected" : ""}>高 (High)</option>
              <option value="max" ${effort === "max" ? "selected" : ""}>最大 (Max)</option>
            </select></label>
          </div>
          <div class="row compact-row">
            <label>连接方式<select name="connection"><option value="auto" ${a.connection === "auto" ? "selected" : ""}>自动发现本机 CLI</option><option value="path" ${a.connection === "path" ? "selected" : ""}>指定本机原生 CLI 路径</option></select></label>
            <label>默认审查模式<select name="review_mode">
              <option value="auto" ${reviewMode === "auto" ? "selected" : ""}>独立测试通过后自动完结</option>
              <option value="interactive" ${reviewMode === "interactive" ? "selected" : ""}>暂停等待人工审查确认</option>
            </select></label>
          </div>
          <label class="path-label" ${a.connection === "path" ? "" : "hidden"}>CLI 可执行文件绝对路径（支持粘贴文件或所在目录）<input name="executable" value="${e(a.executable)}" placeholder="如 C:\\Users\\...\\qoderclicn.exe" autocomplete="off"></label>`
      }<div class="actions">${button("保存配置", "save", 'type="submit" class="btn-primary"')}${button("检查连接", "probe", `type="button" data-id="${e(a.provider)}"`)}</div>${probeCard(data.probes[a.provider])}</form>`;
    })
    .join("")}</div>`;
}

function settings() {
  return `<section class="panel"><h2>AHCP-1 极简 Agent 间通信协议与模板填充规范</h2><p>传入给每个 Agent 的任务目标与验证器契约保持 100% 完整，绝不删减提示词以避免理解偏差；而在 Agent 返回及相互通信时采用英文单字母紧凑键以显著降低输出 Token 消耗：</p><dl><dt>1. Worker 紧凑输出格式 (节省约 50% 结构 Token)</dt><dd>Worker 仅需输出英文紧凑 JSON：<code>{"s":"concise summary","f":[{"p":"stats.py","c":"..."}]}</code> 或行式 <code>AHCP1|RES|...</code>，由 <code>scripts/ahcp_codec.py</code> 与 <code>wire.py</code> 自动填充为完整的 <code>agent-helm.worker-result.v2</code> 模板。</dd><dt>2. Leader / Sub-Reviewer 紧凑审查格式</dt><dd>Reviewer 仅需输出 <code>{"d":"A|R|E","h":"&lt;hash&gt;","r":["reason"],"e":["D","V"]}</code>（<code>A</code>=通过、<code>R</code>=通知 Worker 返工、<code>E</code>=升级），自动填充为标准审查决议。</dd><dt>3. 最终中文汇总报告</dt><dd>所有英文内部通信帧在执行完毕后统一生成面向用户的中文编排报告（含模式说明、拆解判断依据、角色执行结论与验证状态）。</dd><dt>4. 运行状态根目录</dt><dd><code>${e(data.state_root || "未连接")}</code></dd></dl></section><section class="panel"><h2>Claude 独立预算账本</h2><p>每次推理前从本地账本预留。失败不自动退款；费用不确定时停止并人工核对。CLI 估算不能保证代理账单硬上限。</p><form id="budget-form"><div class="row"><label>本次新增总额度（USD）<input name="total" type="number" min="0.01" step="0.01" value="0.30" required></label><label>单次最高（USD）<input name="per_call" type="number" min="0.01" step="0.01" value="0.10" required></label></div><label class="check"><input name="confirmed" type="checkbox" required>我明确授权此新增额度；创建账本本身不会调用模型。</label><div class="actions">${button("确认创建新账本", "budget", 'type="submit" class="btn-primary"')}${button("读取账本状态", "budget-status", 'type="button"')}</div></form></section>`;
}

function approval() {
  const orch = proposal.plan?.orchestration;
  return `<div class="overlay"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="approval-title"><p class="eyebrow">HUMAN APPROVAL GATE</p><h2 id="approval-title">审阅 Leader 编排计划与 Worker 角色分配</h2>${
    orch
      ? `<div class="probe-card" style="margin-bottom:12px">
          <div class="probe-badges">
            <span class="pill info">${e(orch.mode === "decompose" ? "模式二：Leader 拆解/多角色编排" : "模式一：Leader 直派单 Worker")}</span>
            <span class="pill ${orch.splittable ? "success" : "warn"}">${e(orch.splittable ? "已拆解为多角色子任务" : "原子任务 · 原话直通上游 Worker")}</span>
            <span class="pill">Leader: ${e(orch.leader)}</span>
          </div>
          <p style="margin:4px 0 0;font-size:12.5px"><strong>拆解评估：</strong>${e(orch.decomposition_reason)}</p>
        </div>`
      : ""
  }<pre>${json({ plan: proposal.plan, agent_configuration: proposal.agent_configuration, plan_hash: proposal.plan_hash, runtime_hash: proposal.runtime_hash })}</pre><form id="approve-form"><label class="check"><input type="checkbox" name="confirmed" required>我已审阅此计划、Leader 拆解方案和连接配置，授权本次执行。</label><div class="actions">${button("确认并启动执行", "approve", 'type="submit" class="btn-primary"')}<button type="button" data-action="close">稍后处理</button></div></form></section></div>`;
}

function details() {
  const s = detail.status;
  const zh = detail.chinese_report;
  const zhBanner = zh
    ? `<div class="probe-card" style="margin-bottom:16px;background:rgba(0,113,227,0.04);border-color:rgba(0,113,227,0.18)">
        <div class="probe-badges">
          <span class="pill info">${e(zh.orchestration_mode_zh)}</span>
          <span class="pill ${zh.final_state === "accepted" ? "success" : "warn"}">${e(zh.final_state_zh)}</span>
          <span class="pill">Agent 间语言: ${e(zh.inter_agent_language || "en")} (${e(zh.protocol)})</span>
          <span class="pill info">最终 Leader 语言: ${e(zh.final_leader_language || "zh-CN")}</span>
        </div>
        <p style="margin:6px 0;color:#1d1d1f"><strong>Leader 最终中文拆解与调度说明：</strong>${e(zh.decomposition_explanation_zh)}</p>
        ${
          zh.leader_final_response_zh
            ? `<pre style="margin:8px 0;padding:10px 12px;background:#fff;border-radius:10px;border:1px solid rgba(0,113,227,0.15);white-space:pre-wrap;font-size:12.5px;color:#1d1d1f">${e(zh.leader_final_response_zh)}</pre>`
            : ""
        }
        ${
          zh.tasks_zh?.length
            ? `<div style="display:grid;gap:6px;margin-top:8px">${zh.tasks_zh
                .map(
                  (t: any) =>
                    `<div style="font-size:12.5px;padding:8px 12px;background:#fff;border-radius:10px;border:1px solid rgba(0,0,0,0.06)">
                      <strong>节点 ${e(t.task_id)}</strong> · 角色: <code>${e(t.role)}</code> · 执行器: <code>${e(t.worker)}</code>${t.sub_reviewer ? ` · 次级审查: <code>${e(t.sub_reviewer)}</code>` : ""} · 尝试 ${e(t.attempt_count)} 次 · Leader 裁决: <strong>${e(t.leader_decision_zh)}</strong><br>
                      <span style="color:#6e6e73">Worker 英文返回 (EN): <code>${e(t.worker_return_en || "-")}</code></span><br>
                      <span style="color:#1d1d1f"><strong>Leader 最终中文结论 (ZH):</strong> ${e(t.leader_summary_zh || t.summary_zh || "")}</span>
                    </div>`,
                )
                .join("")}</div>`
            : ""
        }
      </div>`
    : "";
  const evidence = detail.evidence
    .map(
      (ev: any) =>
        `<article class="evidence-card"><h3>任务节点：${e(ev.task_id)}</h3>${ev.attempts
          .map((a: any) => {
            const wr = a.worker_result;
            const thinkingInfo = wr?.thinking
              ? [
                  wr.thinking.model ? `模型: ${wr.thinking.model}` : "",
                  wr.thinking.context_window
                    ? `上下文: ${wr.thinking.context_window}`
                    : "",
                  wr.thinking.reasoning_effort
                    ? `思考强度: ${wr.thinking.reasoning_effort}`
                    : "",
                  wr.extensions?.role ? `角色: ${wr.extensions.role}` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")
              : "";
            const wireFrame = wr?.extensions?.wire_frame;
            const hops: any[] = a.agent_hops || wr?.extensions?.agent_hops || [];
            const hopsHtml = hops.length
              ? `<div class="agent-hops-panel">
                  <p class="hops-title">Agent 间交互流转 · 中间过程监控与净结果传递（中间思考过程仅向用户展示，Agent 之间仅传递英文净结果）</p>
                  <div class="hops-list">
                    ${hops
                      .map((h: any) => {
                        const isZh = h.language === "zh-CN";
                        const ts = h.thinking_state || {};
                        return `<div class="hop-card ${isZh ? "hop-final-zh" : "hop-en"}">
                          <div class="hop-header">
                            <span class="hop-step">Step ${e(h.step)}</span>
                            <strong class="hop-route">${e(h.from_agent)} ➔ ${e(h.to_agent)}</strong>
                            <span class="pill ${isZh ? "info" : ""}">${isZh ? "ZH-CN · Leader 最终中文返回" : "EN · 仅传英文净结果 (Result Only)"}</span>
                            <span class="pill success">🧠 ${e(ts.badge || "思考完成")}</span>
                          </div>
                          <div class="hop-thinking">
                            <strong>👁️ 中间过程与思考状态（仅向用户展示 · 不传给下游 Agent）：</strong>
                            <span>模型: <code>${e(ts.model || "-")}</code> · 思考强度: <code>${e(ts.reasoning_effort || "standard")}</code> · 上下文: <code>${e(ts.context_window || "default")}</code></span>
                            <div class="hop-thought-text">${e(ts.thought || "")}</div>
                          </div>
                          <div class="hop-return">
                            <strong>${isZh ? "📢 Leader 面向用户的最终中文返回：" : "📤 传给下一跳 Agent 的净结果 (English Result Only · 无中间过程)："}</strong>
                            <div class="hop-return-body">${e(h.return_content || "")}</div>
                            ${h.wire_frame ? `<small class="hop-wire">AHCP-1 净结果通信帧: <code>${e(h.wire_frame)}</code></small>` : ""}
                          </div>
                        </div>`;
                      })
                      .join("")}
                  </div>
                </div>`
              : "";
            return `<section class="attempt-box"><div class="section-head"><p class="attempt-title">第 ${e(a.attempt)} 次尝试 · <strong>${e(stateLabel(a.state))}</strong></p><div class="evidence-gates"><span class="pill ${a.verification_passed ? "success" : "warn"}">独立测试 ${a.verification_passed ? "通过" : "未通过"}</span><span class="pill ${a.scope_ok ? "success" : "warn"}">修改范围 ${a.scope_ok ? "受控" : "越界"}</span>${wr?.protocol ? `<span class="pill info">${e(wr.protocol)}</span>` : ""}</div></div>${thinkingInfo ? `<p class="thinking-meta">${e(thinkingInfo)}</p>` : ""}${wr?.summary ? `<p class="worker-summary"><strong>Worker 英文返回摘要 (EN)：</strong>${e(wr.summary)}</p>` : ""}${wireFrame ? `<small style="display:block;margin-bottom:8px;color:#0071e3">Worker AHCP-1 英文通信帧：<code>${e(wireFrame)}</code></small>` : ""}${hopsHtml}<pre>${e(a.diff || "尚无候选差异")}</pre><small>候选快照哈希 (SHA-256)：${e(a.candidate_hash || "尚未生成")}</small></section>`;
          })
          .join("")}</article>`,
    )
    .join("");
  return `<section class="panel" id="detail"><div class="section-head"><div><h2>执行证据链、Agent 间思考流转与 Leader 最终中文报告 · ${e(stateLabel(s.state))}</h2><p>各 Agent 之间返回内容严格使用英文 AHCP-1 紧凑协议（含每个节点的思考状态与返回载荷），最终仅由 Leader 返回中文总结。</p></div>${button("刷新证据", "detail", `data-id="${e(s.run_id || s.id)}"`)}</div>${zhBanner}${evidence || "<p>等待执行器生成候选和验证证据...</p>"}<details><summary>展开完整协议包与审查记录 (JSON)</summary><pre>${json({ chinese_report: detail.chinese_report, tasks: s.tasks, events: detail.events, evidence: detail.evidence })}</pre></details><div class="actions">${button("请求取消", "cancel", `data-id="${e(s.run_id || s.id)}"`)}${s.state === "queued" ? button("确认继续返工", "resume", `data-id="${e(s.run_id || s.id)}" class="btn-primary"`) : ""}${s.state === "accepted" ? button("集成到独立验收目录", "integrate", `data-id="${e(s.run_id || s.id)}" class="btn-primary"`) : ""}</div>${
    s.state === "reviewing"
      ? detail.evidence
          .map((ev: any, i: number) => {
            const candidate = ev.attempts?.at(-1);
            return candidate
              ? `<form class="review-form" data-task="${e(s.tasks[i].id)}" data-hash="${e(candidate.candidate_hash)}"><h3>人工 Leader 审查裁决：${e(s.tasks[i].id)}</h3><select name="decision"><option value="accept">通过 (Accept · 须独立测试与范围校验通过)</option><option value="rework">拒绝并通知 Worker 返工 (Rework)</option><option value="escalate">升级为人工介入 (Escalate)</option></select><label class="check"><input type="checkbox" name="confirmed" required>我已核对以上代码 Diff 与独立测试结果。</label>${button("提交审查裁决", "review", 'type="submit" class="btn-primary"')}</form>`
              : "";
          })
          .join("")
      : ""
  }</section>`;
}

function collectAgentProfile(form: HTMLFormElement, autoEnable = false): Agent {
  const f = new FormData(form);
  const old = data.agents.find((a) => a.provider === form.dataset.provider)!;
  const customModel = String(f.get("custom_model") || "").trim();
  const selectedModel = String(f.get("model") || "").trim();
  const executable = String(f.get("executable") || "").trim();
  const connection =
    String(f.get("connection") || "auto") === "path" ||
    (executable && old.connection !== "path")
      ? String(f.get("connection") || "auto")
      : "auto";
  const hasEnabled =
    old.provider === "mock" ||
    f.has("enabled") ||
    (autoEnable && Boolean(executable || customModel || selectedModel));
  return {
    ...old,
    enabled: hasEnabled,
    model: customModel || selectedModel,
    connection,
    executable,
    context_window: String(
      f.get("context_window") ?? old.context_window ?? "",
    ).trim(),
    reasoning_effort: String(
      f.get("reasoning_effort") ?? old.reasoning_effort ?? "",
    ).trim(),
    review_mode: String(f.get("review_mode") ?? old.review_mode ?? "auto").trim(),
  };
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
  app
    .querySelector<HTMLSelectElement>("#orch-mode-select")
    ?.addEventListener("change", (ev) => {
      orchMode = (ev.currentTarget as HTMLSelectElement).value;
      const matrix = app.querySelector<HTMLElement>(".role-matrix");
      if (matrix) matrix.hidden = orchMode !== "decompose";
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
            const form = el.closest("form.agent") as HTMLFormElement | null;
            if (form && id !== "mock") {
              const profile = collectAgentProfile(form, false);
              await request("probe_agent", { provider: id, profile });
            } else {
              await request("probe_agent", { provider: id });
            }
            await refresh();
            notice = `${id} 连接探测已完成，当前表单配置已同步保存。`;
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
      const modeVal = String(f.get("mode") || "direct");
      const leaderVal = String(f.get("leader") || "");
      const subRevVal = String(f.get("sub_reviewer") || "");
      const payload: Record<string, any> = {
        goal: f.get("goal"),
        provider: f.get("provider"),
        fault_demo: f.has("fault_demo"),
      };
      if (modeVal && modeVal !== "direct") {
        payload.mode = modeVal;
      }
      if (leaderVal && leaderVal !== "mock") {
        payload.leader = leaderVal;
      }
      if (subRevVal) {
        payload.sub_reviewer = subRevVal;
      }
      if (modeVal === "decompose") {
        payload.roles = [
          {
            role: String(f.get("role_1_name") || "implementer").trim(),
            provider: String(f.get("role_1_worker") || f.get("provider")),
            parallel: f.has("role_1_parallel"),
          },
          {
            role: String(f.get("role_2_name") || "verifier-guard").trim(),
            provider: String(f.get("role_2_worker") || f.get("provider")),
            parallel: f.has("role_2_parallel"),
          },
        ];
      }
      void perform(async () => {
        const p = await request("create_task", payload);
        taskDraftDirty = false;
        proposal = await request("proposal", { proposal_id: p.proposal_id });
        await refresh();
      });
    });
  app.querySelectorAll<HTMLFormElement>("form.agent").forEach(
    (form) =>
      (form.onsubmit = (ev) => {
        ev.preventDefault();
        const profile = collectAgentProfile(form, true);
        void perform(async () => {
          await request("save_agent", { profile });
          await refresh();
          notice = `${profile.name} 配置已保存并启用，可直接用于新任务。`;
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
