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

元数据实测：installed=true、compatible=true、authenticated=true；读取的只是原生 CLI 版本与登录状态，没有进行本轮付费推理。上一轮独立样例经 CC Switch 调用的是 GLM，旧审查因契约失败不能当作新 worker 的真实验收。

新增 16 项离线测试覆盖原生字段、拒绝文本 fallback、终态/费用/权限检查、精确 cents 预算、并发事务预留、总额耗尽、未知结果暂停、估算超限暂停、版本漂移、显式开关、领取前预算阻断，以及固定 flags/stdin。默认测试使用 fake runner，不启动真实推理。

全量回归共 129 项：Windows 128 项通过、1 项因 symlink 权限跳过；WSL 129 项全部通过。日志保留在仓库之外。

真实 worker 闭环尚待新增预算确认。授权后应在全新合成仓库运行 `scripts/verify_cross_provider.py --brain codex --worker claude --claude-budget-ledger ...`；原始证据和账本全部保留在仓库外。

官方依据：

- [原生结构化输出与 structured_output](https://code.claude.com/docs/en/headless)
- [CLI 参数、回合与预算限制](https://code.claude.com/docs/en/cli-reference)
