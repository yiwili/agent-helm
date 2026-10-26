# Agent Helm

订阅优先的本地、单用户 Agent 调度器，Python 3.11+，运行和测试均无第三方依赖。

当前交付是 **v0.2 夹具限定的可运行首版**：Mock / Manual / Codex CLI，结构化规划与审查，SQLite 状态、DAG、审批与幂等、独立副本、固定测试、候选哈希、取消/超时、证据导出、独立集成副本、一致性备份与校验恢复。真实 Codex worker 和新的独立 Codex reviewer 已在 Windows 的合成数据上联调通过，并完成本机取消演练。尚未达到指导书全部 P1–P7 的生产验收标准；具体差距见 [能力记录](docs/capabilities.md)。

实验只允许本项目和新生成的合成夹具，公司业务代码、业务仓库和备份不得用作实验输入。公开仓库不包含本地运行证据或凭据。

**当前只注册 `fixture-stats` 验证器；请只运行自建、无敏感数据、可信的夹具。** 验证器先用严格 AST 白名单限制为纯 `total(values)` 求和函数，再执行固定测试；导入、属性访问、文件操作、任意函数调用和进程退出均被拒绝。进程树监督与文件副本不是 OS 安全沙箱。同一 OS 用户可编辑数据库、源文件和审批，CLI 审批入口不向 worker 暴露。禁止据此用于恶意代码或生产业务库。

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

`run` 默认 mock reviewer，仅允许 mock worker。真实或 Manual worker 必须使用 `--brain codex` 或 `--brain interactive`。人工流程：worker provider 选 manual，`--manual-result response.json` 提交符合 `result.schema.json` 的候选内容，`--brain interactive` 将任务停在 reviewing；读取证据后用 `review RUN_ID --task TASK_ID --decision review.json` 提交人工决策。文件变化或测试失败均不能 accept。审批等待不无限续租，过期恢复需人工核对。

故障演示：`demo --failure bad`（三次失败后升级人工）、`demo --failure quota`、`demo --failure permission`、`demo --failure timeout`；失败/阻塞的 CLI 退出码为 2。`cancel RUN_ID` 可从第二个终端取消；运行中的进程确认停止前保持 cancel_requested。离线测试还覆盖进程子树、租约、重启、数据库事务回滚、错误 JSON、路径越界和基线漂移。

## 实现约束

- 每 run 一个顺序调度者，每 task/attempt 独立快照；DAG 输入不要求按拓扑顺序排列。首版尚无全局双并发服务或 daemon；聊天退出后提交记录保留，执行需另起持久终端运行 `run`，进程异常中止后不会自动重新执行。
- SQLite 原子领取与 fencing token 防止旧执行提交。超期租约进入 needs_reconciliation，禁止盲目重试未知副作用。`recover` 只分类，不假装恢复供应商会话。
- worker 默认禁止进一步委派；Codex 配置关闭 multi_agent、忽略用户 config；参数数组与 stdin 启动，不使用 shell 拼接。Windows 用 kill-on-close Job Object，Linux 用进程组。不能阻止恶意进程逃离 OS 权限边界，仍需专用账号/容器才能运行不可信任务。
- 原目录不 stash/reset/清理。快照排除 `.git` 和缓存，用完整文件清单与模式计算树哈希；拒绝 symlink/junction、大小写冲突和危险路径。当前不支持删除/重命名/二进制/submodule 候选，不提供通用 Git 补丁合并。
- 固定外部验收脚本不能由 worker 改写，基线和候选使用同一脚本。测试移除模型 API 环境凭据，仍继承普通本机权限，因此只执行可信夹具。
- API、GUI、ACP、第二个真实 provider、自动合并/推送/发布、跨环境 runner 未实现且关闭。配置示例说明实际限制，不会通过改 JSON 自动获得接口。

架构和验收细节见 [能力与验证记录](docs/capabilities.md)、[运行与恢复](docs/operations.md)、[设计取舍](docs/decisions.md)。

## 可靠性验证

默认离线测试共 62 项；已在 Windows 和 WSL 验证。Windows 的 symlink 创建测试可能因权限跳过。故障回放使用 `tests/fixtures/codex` 中明确标记的离线 fake CLI，不接触真实账号。

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
