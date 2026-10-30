# Agent Helm

订阅优先的本地、单用户 Agent 调度器，Python 3.11+，运行和测试均无第三方依赖。

**新增 Tauri 2 响应式桌面工作台**：支持子 Agent 模型切换、原生 CLI 自动发现/指定路径、计划审批、执行证据与独立预算。Windows 一键构建入口为 `desktop/build.cmd`；开发、连接范围与构建说明见 [桌面指南](docs/desktop.md)。GUI 有独立的 Node/Rust 构建依赖，Python 核心运行与默认测试仍仅用标准库。本次不验证打包产物，Windows 桌面 CI 仅做开发检查。

**v0.7.0 增加可供 AI 调用的 MCP 服务和原生可执行打包。** Windows 双击根目录 `build.cmd`，自动生成带校验摘要的可执行包，并验收 MCP 流程（区别于上述不运行产物的桌面构建入口）。当前按用户要求只推进 Windows，原生打包 GitHub Actions 改为手动触发；其他平台保留 `build.sh` 和手动 all 构建入口。使用方式与客户端配置见 [MCP 与打包指南](docs/mcp-packaging.md)。

当前交付是 **v0.7.0 本地单用户版本**：Mock / Manual / Codex CLI / Qoder CN CLI，以及需单独预算的 Claude CLI 下游 worker。包含 Git 基线、结构化规划与审查、诊断里程碑、返工反馈、SQLite 调度、审批、独立副本、可信验证器、候选哈希、取消恢复及校验备份。Codex 可以作为主脑或下游 worker；Claude 原生结构化输出已实测，原始候选重放验收通过，但故障返工链路未通过完整验收。Cursor 的可选代码保持默认关闭，当前不推进其验收。已在合成 Git 夹具上验证 Codex/Qoder 两个主脑与 worker 方向。尚未达到指导书全部 P1–P7 的生产验收标准；具体差距见 [能力记录](docs/capabilities.md)。

实验只允许本项目和新生成的合成夹具，公司业务代码、业务仓库和备份不得用作实验输入。公开仓库不包含本地运行证据或凭据。

**验证实验只用自建、无敏感数据、可信的夹具。** 内置 `fixture-stats` 先用 AST 白名单限制为纯 `total(values)` 函数，再运行固定测试；也可由本地用户显式登记独立验证程序，绑定 argv、脚本和可执行文件哈希。自定义验证器可能执行候选代码，不能继承内置 AST 的安全保证。进程树监督与副本不是 OS 沙箱；当前不用于不可信代码或生产业务库。

## 快速运行

PowerShell，在项目目录执行（优先使用现有 Codex Python runtime；也可设置 `AGENT_ORCH_PYTHON` 为 Python 3.11+ 的绝对路径）：

```powershell
.\agent-orch.ps1 doctor --read-only
.\agent-orch.ps1 --state-root 'D:\AI-Temp-Folder\agent-helm-demo' demo --bad-first
```

第二条完全离线，第一次 worker 假报成功，固定测试拒绝，第二次修复后接受；所有主脑决策明确为 mock。运行数据在 state root，不修改本项目或其他业务仓库。

WSL / Linux，不安装依赖：

```sh
sh ./agent-orch doctor --read-only
sh ./agent-orch --state-root "$HOME/.local/state/agent-helm-demo" demo --bad-first
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Windows 测试：

```powershell
$env:PYTHONPATH = "$PWD\src"
& $env:AGENT_ORCH_PYTHON -m unittest discover -s tests -v
```

若未设置 `AGENT_ORCH_PYTHON`，可直接用已安装 Python 的绝对路径代替。Windows 当前 `python` 命令可能只是 Microsoft Store 占位程序；`doctor` 会显示实际运行版本。

可选安装：`python -m pip install --no-deps .`，此步骤会按 `pyproject.toml` 获取锁定的构建依赖。源码启动及测试不需要 pip，也不会自动安装软件。安装后可使用 `agent-orch` 命令。

## 显式调用真实 Codex

```powershell
.\agent-orch.ps1 providers probe codex --mode metadata
.\agent-orch.ps1 --state-root 'D:\AI-Temp-Folder\agent-helm-live' demo --real-codex
```

第二条明确消费当前 Codex 官方 ChatGPT 登录的额度，只使用生成的合成夹具。CLI 版本必须精确匹配 `codex-cli 0.164.0-alpha.11.1`；版本漂移会拒绝调用。不会改登录、不读取 token、不使用 API key、不回退收费 API。默认真实 provider 关闭。

worker 通过 stdin 收到任务与 `stats.py`，在 `read-only` sandbox 返回完整文件内容的 JSON；调度器校验允许路径后应用到独立副本。验证器独立执行固定的空列表、正数、负数、零和小数用例。reviewer 在新 CLI 会话读取原目标、实际 diff、基线与候选测试，返回绑定候选哈希的 JSON。接受后只创建集成候选并复测，不写回用户源码。

本地登录类型不是账单核实：`billing_verified` 和 `production_ready` 仍为 false，剩余额度与金额未知为 null。`--real-codex` / `--enable-codex` 是按次显式试运行，不启用无人值守自动路由。`policy.select_provider` 只有在计费证据齐全时才建议订阅自动路由；当前调度执行的是计划中的显式 provider。

## 规划、审批、执行与人工审查

交互主脑可按 `examples/tasks/fix.json` 编写计划，或调用真实规划入口：

```powershell
.\agent-orch.ps1 fixture --output 'D:\AI-Temp-Folder\helm-fixture'
# request.json 是用户需求 JSON；plan 的输出必须是新文件。
.\agent-orch.ps1 plan --request request.json --output plan.json --brain codex --enable-codex --workspace 'D:\AI-Temp-Folder\helm-fixture'
.\agent-orch.ps1 approve --plan plan.json --source 'D:\AI-Temp-Folder\helm-fixture'
# 将上一条返回的 scope_id 和下面返回的 run_id 代入后续命令。
.\agent-orch.ps1 submit --plan plan.json --source 'D:\AI-Temp-Folder\helm-fixture' --approval-scope SCOPE_ID --idempotency-key my-request-001
.\agent-orch.ps1 run RUN_ID --enable-codex --brain codex
.\agent-orch.ps1 status RUN_ID
.\agent-orch.ps1 events RUN_ID --after-seq 0
.\agent-orch.ps1 review RUN_ID --show-evidence
.\agent-orch.ps1 integrate RUN_ID
.\agent-orch.ps1 export RUN_ID --output 'D:\AI-Temp-Folder\helm-evidence'
```

不指定 `--brain codex` 时，`plan` 只校验并保存交互主脑提交的结构化计划，不伪造模型推理。`approve` 默认要求本地终端输入 APPROVE；自动化测试的 `--yes` 是可信本地用户的显式审批开关，不应暴露给模型。审批绑定计划哈希、源码目录与有效期；修改计划后需重新审批。`revoke SCOPE_ID` 阻止后续领取任务。

`run` 默认 mock reviewer，仅允许 mock worker。真实或 Manual worker 必须使用 `--brain codex`、`--brain qoder-cn` 或 `--brain interactive`。人工流程：worker provider 选 manual，`--manual-result response.json` 提交符合 `result.schema.json` 的候选内容，`--brain interactive` 将任务停在 reviewing；读取证据后用 `review RUN_ID --task TASK_ID --decision review.json` 提交人工决策。文件变化或测试失败均不能 accept。审批等待不无限续租，过期恢复需人工核对。

## Qoder CN 跨产品流程

Qoder CN CLI 必须精确匹配 `1.1.80` 并完成其官方登录。提供原生可执行文件绝对路径；不接受 shell 包装器。默认不启用真实调用，不把 Qoder 返回的零 token/金额字段解释为免费，实际 credits 记录在私有证据中。

```powershell
.\agent-orch.ps1 providers probe qoder-cn --qoder-cn-bin $env:AGENT_ORCH_QODER_CN
# 显式消费两个产品的额度；只生成新的无敏感 Git 夹具。
$env:PYTHONPATH = "$PWD\src"
& $env:AGENT_ORCH_PYTHON scripts/verify_cross_provider.py --output "$env:TEMP\helm-cross-provider-new" --qoder-cn-bin $env:AGENT_ORCH_QODER_CN --inject-first-fault
```

`--output` 必须不存在，且位于源码仓库之外。此脚本包含可信测试程序的显式审批，仅用于合成样例。`--inject-first-fault` 会保存真实 worker 原响应，再由测试程序故意破坏首轮候选以验收返工；不能据此声称模型出错。省略该选项则不注入错误。用 `--brain qoder-cn --worker codex` 交换角色；最新验收见 [worker 接入记录](docs/worker-acceptance.md)。

真实规划要求 `milestone_review=true`：worker 先诊断，主脑批准后才实现；越界诊断停止并要求新计划/审批。返工携带绑定前一候选哈希的测试、diff 和审查反馈；基线未变时复用已批准诊断。执行期间续租，租约丢失需核对。它仍不是通用交互暂停/恢复系统。

此前 Claude CLI 经 CC Switch 自定义 API 的独立小样本审查记录见 [阶段 3 验收](docs/phase3-acceptance.md)；旧审查未通过结构化契约。新的 worker 实现改用原生 structured_output，状态见 [Claude worker 接入](docs/claude-worker-acceptance.md)。TRAE 按用户要求跳过。

## Claude 与 Codex 下游 worker

`plan --worker-provider codex|claude|qoder-cn` 显式选择执行产品，`--brain codex|qoder-cn` 选择主脑。Claude 当前仅接入 worker，不是自动 API fallback。Claude CLI 可以使用已有 CC Switch 配置，底层模型未必为 Anthropic Claude；实际 modelUsage 与 CLI 费用估算记录在私有证据中。

真实 Claude 调用必须同时具备 `--enable-claude` 和用户显式授权的外部预算账本。原有 `plan.api_budget=0` 仍禁止自动 API 路由；显式选择的 Claude CLI 可能经自定义 API 收费，由独立账本约束。预算创建命令属于可信本地用户，不能作为模型工具开放。

```powershell
# 仅在取得本轮明确预算授权后执行；账本必须是新文件，位于仓库、源目录和工作副本之外。
.\agent-orch.ps1 budgets create --ledger "$env:TEMP\helm-claude-budget-new.db" --total-usd 0.30 --per-call-usd 0.10
# 显式消费 Codex 额度与已授权 Claude 预算；只生成新的合成 Git 样例。
& $env:AGENT_ORCH_PYTHON scripts/verify_cross_provider.py --output "$env:TEMP\helm-claude-live-new" --brain codex --worker claude --claude-budget-ledger "$env:TEMP\helm-claude-budget-new.db"
.\agent-orch.ps1 budgets status --ledger "$env:TEMP\helm-claude-budget-new.db"
```

CLI 自动发现原生 `claude` 或用户 `.local/bin/claude.exe`，也可传 `--claude-bin`。精确版本为 `2.1.311 (Claude Code)`。手动提交计划时 provider 选 `claude`，run 加 `--enable-claude --claude-budget-ledger ... --brain codex --enable-codex`。不开启或缺少账本会在领取 attempt 前阻断。

一次调用指一次 CLI 推理进程，最多 3 个 agent turns，原生 JSON Schema 内部生成也计入限制。stdin 传任务，safe-mode 关闭定制，禁用业务工具、skills、MCP 和持久会话；仅加载 user 设置以保留现有认证/模型，不提取 token。只接受成功终态的原生 `structured_output`，缺失时不从 result 文本或 Markdown 提取候选。候选仍由独立固定测试与主脑审查验收。

规划、诊断、执行及审查均携带可信 verifier contract，说明 AST 限制与五个固定用例，不能额外要求 None 输入兼容。上下文发生变化时不复用旧里程碑；审批计划与实际验证器不改写。本轮真实调用暴露了这一上下文缺口，修复后已做离线回归，尚未重新付费跑完整返工链路。

账本通过 SQLite 事务先预留完整单次上限，失败也不自动退回；中断等未知结果、CLI 估算超预留会暂停预算并要求核对。即使单次只报告少量费用，也不自动扩充调用次数。CLI `--max-budget-usd` 和本地预留都不能证明代理账单硬上限；账本不代表付款系统。

## 可选 Cursor worker（当前不推进）

Cursor 使用官方 CLI 包中的原生 `node.exe`（Linux 为 `node`）和 `index.js`，以 argv 直接启动，不通过 `.cmd`、PowerShell 或任意 shell 命令配置。版本必须为 `2026.10.22-27dcf5f`。先将 `AGENT_ORCH_CURSOR_DIR` 设置为官方包目录，`AGENT_ORCH_CURSOR_CONFIG` 设置为仓库和工作副本之外的全新私有目录：

```powershell
.\agent-orch.ps1 providers prepare-cursor --config-dir $env:AGENT_ORCH_CURSOR_CONFIG
# 只为下面的官方登录命令设置独立配置目录；不会复制其他产品的 token。
$previousCursorConfig = $env:CURSOR_CONFIG_DIR
try {
    $env:CURSOR_CONFIG_DIR = $env:AGENT_ORCH_CURSOR_CONFIG
    & "$env:AGENT_ORCH_CURSOR_DIR\node.exe" "$env:AGENT_ORCH_CURSOR_DIR\index.js" login
} finally {
    $env:CURSOR_CONFIG_DIR = $previousCursorConfig
}
.\agent-orch.ps1 providers probe cursor --cursor-cli-dir $env:AGENT_ORCH_CURSOR_DIR --cursor-config-dir $env:AGENT_ORCH_CURSOR_CONFIG
& $env:AGENT_ORCH_PYTHON scripts/verify_cross_provider.py --output "$env:TEMP\helm-cursor-new" --brain codex --worker cursor --cursor-cli-dir $env:AGENT_ORCH_CURSOR_DIR --cursor-config-dir $env:AGENT_ORCH_CURSOR_CONFIG
```

最后一条是显式真实调用，不属于默认测试。手动提交计划时使用 provider `cursor`，执行加 `--enable-cursor --cursor-cli-dir ... --cursor-config-dir ... --brain codex --enable-codex`。未启用时阻断且不领取 attempt。

独立配置拒绝 Shell、Read、Write、WebFetch 与 MCP，启用 ask/sandbox；不能导入用户扩展、自定义端点或工作副本 `.cursor` 设置。配置变化立即阻断。stdin 传任务，要求 stream-json 中官方登录、完整成功终态、同一 session 和精确候选 JSON；遇到任何工具事件拒绝候选。`--trust` 仅免除合成工作副本的信任交互，不使用 `--force`、`--yolo` 或自动批准 MCP。以上控制与本地进程监督不是完整 OS 隔离，Cursor 的工具拒绝效果还需真实验收，不能仅凭 ask 模式称沙箱已验证。

故障演示：`demo --failure bad`（三次失败后升级人工）、`demo --failure quota`、`demo --failure permission`、`demo --failure timeout`；失败/阻塞的 CLI 退出码为 2。`cancel RUN_ID` 可从第二个终端取消；运行中的进程确认停止前保持 cancel_requested。离线测试还覆盖进程子树、租约、重启、数据库事务回滚、错误 JSON、路径越界和基线漂移。

## 实现约束

- 每 run 一个顺序调度者，每 task/attempt 独立快照；DAG 输入不要求按拓扑顺序排列。首版尚无全局双并发服务或 daemon；聊天退出后提交记录保留，执行需另起持久终端运行 `run`，进程异常中止后不会自动重新执行。
- SQLite 原子领取与 fencing token 防止旧执行提交。超期租约进入 needs_reconciliation，禁止盲目重试未知副作用。`recover` 只分类，不假装恢复供应商会话。
- worker 默认禁止进一步委派；Codex 配置关闭 multi_agent、忽略用户 config；参数数组与 stdin 启动，不使用 shell 拼接。Windows 用 kill-on-close Job Object，Linux 用进程组。不能阻止恶意进程逃离 OS 权限边界，仍需专用账号/容器才能运行不可信任务。
- 原目录不 stash/reset/清理。Git 基线记录 HEAD、索引和工作区状态；副本不共享 `.git`，忽略文件不复制。版本 2 候选支持新增/修改、删除、纯重命名、base64 二进制和 POSIX 执行位；拒绝越界与 symlink/junction，不支持 submodule、linked worktree 或通用 Git 补丁自动合并。
- 固定外部验收脚本不能由 worker 改写，基线和候选使用同一脚本。测试移除模型 API 环境凭据，仍继承普通本机权限，因此只执行可信夹具。
- API、GUI、ACP、第二个真实 provider、自动合并/推送/发布、跨环境 runner 未实现且关闭。配置示例说明实际限制，不会通过改 JSON 自动获得接口。

架构和验收细节见 [能力与验证记录](docs/capabilities.md)、[运行与恢复](docs/operations.md)、[设计取舍](docs/decisions.md)。

## 可靠性验证

新增 MCP/打包入口测试后共 149 项离线测试；Windows 的 symlink 创建测试可能因权限跳过。故障回放使用明确标记的离线 fake CLI，不接触真实账号。真实跨产品脚本必须单独显式运行，不属于默认测试。

备份、校验和恢复（示例输出目录均须不存在）：

```powershell
.\agent-orch.ps1 --state-root STATE_ROOT backup --output BACKUP_DIRECTORY
.\agent-orch.ps1 verify-backup --backup BACKUP_DIRECTORY
.\agent-orch.ps1 restore --backup BACKUP_DIRECTORY --output NEW_STATE_ROOT
.\agent-orch.ps1 --state-root NEW_STATE_ROOT status RUN_ID
.\agent-orch.ps1 --state-root NEW_STATE_ROOT integrate RUN_ID
```

恢复验证哈希和 SQLite 完整性，不自动执行任务、不覆盖原 state root；同平台恢复可迁移内部绝对路径，外部 source 目录仍需保留。v0.1 无哈希清单的旧备份不能使用此入口。

真实取消 smoke 必须显式运行，最多启动两次 Codex exec，仅使用脚本生成的夹具：

```sh
PYTHONPATH=src python scripts/verify_codex_lifecycle.py --output NEW_PRIVATE_REPORT_DIRECTORY
```

本机停止证据与远端会话、计费结算严格区分。详细验收门见 [阶段 1 验收清单](docs/phase1-acceptance.md)。

## Git 工作区与登记验证器

```powershell
.\agent-orch.ps1 --state-root PRIVATE_STATE_ROOT demo --git --bad-first
.\agent-orch.ps1 fixture --git --output NEW_SYNTHETIC_SOURCE
```

Git 源必须有 HEAD commit。审批后修改 HEAD、索引或选中文件，需要重新审批；提交后漂移不能启动候选，集成前漂移也会拒绝。源中的 staged、unstaged、untracked 和已删除内容保留；忽略文件不读取、不复制。子模块、冲突索引、Git filters、config includes、共享对象库及 linked worktree 均暂不支持。

自定义验收先由可信本地用户登记，再在计划的 `verifier_profile` 中引用 ID：

```powershell
.\agent-orch.ps1 --state-root PRIVATE_STATE_ROOT verifiers register --profile my-check-v1 --definition PRIVATE_DEFINITION_JSON
```

定义符合 `schemas/verifier.schema.json`，argv 使用绝对可执行文件、仓库外的固定验收脚本，以及一个独立的 `{workspace}` 参数；`pinned_files` 显式列出脚本。示例脚本见 `examples/verifiers/message_check.py`。登记和计划审批默认都需本地终端确认；不要向模型暴露这些入口。profile 不可覆盖，修改验收程序需新 ID、新计划审批；不接受 worker 提供的命令。

`schema_version=2` 的结果使用 `operations`，版本 1 的 `files` 继续兼容。重命名的源、目标都必须获批；Windows 不支持执行位变更。Codex 内置 worker 当前仍输出版本 1 文本文件，版本 2 已由 Manual/契约测试验证。新建 Git 夹具的真实调用需显式开启：

```sh
PYTHONPATH=src python scripts/verify_git_workspace.py --output NEW_PRIVATE_DIRECTORY --real-codex
```

此脚本不接受现有仓库路径，只创建合成 Git 仓库。详见 [阶段 2 验收清单](docs/phase2-acceptance.md)。
