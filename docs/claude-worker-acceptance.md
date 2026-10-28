# Claude / Codex 下游 worker（2026-10-28）

本轮按用户澄清推进 Claude 与 Codex，下游 worker 不要求使用同名底层模型。Cursor 原有可选适配器保持关闭，不再推进登录。实验仅限本项目和新建合成样例，不使用公司业务代码、备份或凭据。

## 实现

Claude CLI `2.1.311 (Claude Code)` 作为显式选择的 worker；Codex 已有真实 worker 闭环证据。主脑仍为 Codex 或 Qoder CN。默认关闭 Claude，不能通过自动路由或故障回退启动它。缺少独立预算账本时，调度器在领取 attempt 前阻断。

CLI 自己加载现有用户认证和模型配置，兼容已配置的 CC Switch。safe-mode、空业务工具集、strict 空 MCP、禁用 skills/commands 和会话持久化，拒绝交互权限提升。既不复制密钥，也不替换认证或模型；auth status 不用来证明实际供应商或免费权益。user 设置可能选择自定义 API，这不是 Anthropic Claude 模型验证。

本轮改变此前失败的纯文本审查路径：使用 `--json-schema` 和原生 `structured_output`，不剥除围栏、不从自然语言抓取 JSON。必须同时校验成功 result、completed、session、回合上限和权限拒绝。候选内容仍要经过本地路径约束、固定验证器、候选哈希与独立主脑审查。离线解析通过不代表上游模型已经能生成该字段。

## 预算

预算账本由本地用户单独创建，既不能位于源代码/工作副本，也不能提交仓库。模型计划不能修改账本路径、总额或单次额度，`plan.api_budget=0` 继续禁用自动 API fallback。显式选择 Claude CLI 的费用另由用户授权。

以 0.30 美元总额和 0.10 美元单次为例，最多预留三个 CLI 推理进程。一个进程最多 3 个 agent turns，可能包含多次 HTTP 请求。事务预留防止并发超配；不因 CLI 只报告很小费用而退回额度。进程异常/中断无法确认结果则记录 unknown 并暂停预算；报告成本超单次预留也暂停，不能自动重试。完整终态但结构化契约失败保留预留及报告成本。

CLI 报价可能与代理账单不同，且它的预算停止检查可能发生在请求完成后。账本与 `--max-budget-usd` 仅控制本地调用，不承诺供应商账单硬上限，`billing_verified=false`。

## 当前证据

元数据实测：installed=true、compatible=true、authenticated=true。新预算授权后实际执行了三次 CLI 推理，均成功返回原生 structured_output，stop_reason 为 tool_use、terminal_reason 为 completed、每次 2 个 agent turns。实际 modelUsage 为 `glm-5.3[1m]`，这是 Claude CLI 经 CC Switch 的验证，不是 Anthropic Claude 模型验证。

新增 16 项离线测试覆盖原生字段、拒绝文本 fallback、终态/费用/权限检查、精确 cents 预算、并发事务预留、总额耗尽、未知结果暂停、估算超限暂停、版本漂移、显式开关、领取前预算阻断，以及固定 flags/stdin。默认测试使用 fake runner，不启动真实推理。

全量回归共 129 项：Windows 128 项通过、1 项因 symlink 权限跳过；WSL 129 项全部通过。日志保留在仓库之外。

## 本轮真实流程结果

Codex 主脑生成计划；Claude CLI 诊断、主脑批准；Claude 首轮实际返回了正确的 `return sum(values)` 候选。可信测试程序另存原始响应后，故意将首轮候选替换为错误的空列表 999，用于检验返工。

故障返工链路**未通过**：Codex reviewer 建议 `sum(values) if values is not None else None`，第二次 Claude 实现照做；固定 AST 验证器因比较语法不在白名单内而拒绝执行。此后下一次推理被预算预留门阻断，run 最终 blocked，没有集成或写回源代码。不能将此链路报告为接受，也不能把注入的 999 当作 Claude 模型失败。

根因是编排器只传了 verifier ID，没有将实际语法限制传给诊断、worker 和 reviewer。v0.6.1 增加可信 verifier context，包含允许语法、比较禁用、确切五用例与非验收项；贯穿规划、诊断、实现、审查和证据。上下文变化后拒绝复用旧里程碑。没有修改 AST 白名单、固定测试或已冻结任务计划，注册 verifier 不继承 fixture-stats 假设。新增 5 项离线测试覆盖实际比较拒绝、上下文/用例一致、全阶段传递、原计划不变与上下文漂移拒绝。修复后的完整真实返工链路尚未重跑。

修复后全量回归共 134 项：Windows 133 项通过、1 项 symlink 权限跳过；WSL 134 项全部通过。补充确切用例说明后，5 项上下文测试再次通过。

另做原始候选重放验收：原始文件与真实 native structured_output 一致，校验其内容哈希及原记录引用后，通过 Manual 重放到全新合成 Git 仓库，独立固定测试、真实 Codex reviewer 和集成复测均通过。明确标注为 recorded-response replay，没有再次调用 Claude，也不是修复后新鲜推理的全链路通过。原失败源仓库和重放源仓库均保持不变。

三次 CLI 报价依次为 **0.044185、0.022825、0.044426 美元**，合计约 **0.111436 美元**；每次均低于 0.10 美元。累计预留 **0.30 美元**，剩余可分配预留为零，之后没有追加 Claude 调用。代理实际账单仍未核实。原始证据、下载工具、预算账本和两类验收报告全部保留在仓库外。

官方依据：

- [原生结构化输出与 structured_output](https://code.claude.com/docs/en/headless)
- [CLI 参数、回合与预算限制](https://code.claude.com/docs/en/cli-reference)
