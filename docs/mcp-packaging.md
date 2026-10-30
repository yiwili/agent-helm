# MCP 服务与可执行打包（v0.7.0）

Agent Helm 提供本地 stdio MCP 服务，运行时仍仅依赖 Python 标准库。可执行包携带 Python 运行库，使用者不需要另装 Python。服务目前只操作它新建的、无敏感数据的 stats 合成夹具；保持本项目的实验范围。

## 直接使用可执行包

解压完整 `agent-helm` 文件夹，保留可执行文件旁的 `_internal`。Windows 执行 `agent-helm.exe`；Linux/macOS 执行 `./agent-helm`。这是解压即用的命令行/MCP 软件，不需要在业务仓库内运行。

启动参数：

```powershell
agent-helm.exe --state-root 'C:\AI-Runtime\agent-helm' mcp
```

MCP 客户端负责启动此进程，通过 stdin/stdout 交换 JSON-RPC。手动启动后它会等待输入。不要把日志或欢迎文字打印到 stdout；服务错误走 stderr。

通用 MCP JSON 配置见 `configs/mcp.example.json`，按安装位置替换 command 与 state-root。Codex 的 `config.toml` 示例：

```toml
[mcp_servers.agent_helm]
command = "C:/Tools/agent-helm/agent-helm.exe"
args = ["--state-root", "C:/AI-Runtime/agent-helm", "mcp"]
```

Linux/macOS 将 command 替换为解压后的可执行文件绝对路径，state-root 替换为仓库外的私有状态目录。没有自动修改现有客户端配置、登录或 CC Switch 设置。

可以给 AI 的指令：

> 使用 Agent Helm 创建一个合成夹具，以 mock worker 验证修复与返工。准备计划后向我展示审批内容与终端命令，等待我批准，再提交、运行、查看证据和生成集成候选。保持业务仓库不变。

## AI 调用流程

| 工具 | 用途 |
|---|---|
| helm_capabilities | 查询范围、宿主启用的 worker 和操作顺序 |
| helm_fixture_create | 生成新夹具与 workspace_id；不接受现有仓库路径 |
| helm_plan_prepare | 提交结构化计划，返回 proposal_id、计划哈希与人工操作说明 |
| helm_proposal_get | 查看计划及审批状态 |
| helm_submit | 幂等提交人工已批准的计划；一个 proposal 对应一个 run |
| helm_run_start | 异步启动一次运行，立即返回 |
| helm_run_get | 查询状态、attempt 和是否需要人工处理 |
| helm_events_get | 按 seq 查询分页进度，不返回原始 provider payload |
| helm_evidence_get | 查看候选 diff、独立测试结果和审查结论 |
| helm_cancel | 请求持久化本机取消；远端停止和结算仍未验证 |
| helm_integrate | 对已接受候选创建独立副本并复测，重复调用返回同一结果 |

MCP 不提供 approve、预算授权、密钥、任意命令/路径、Git merge/push 工具。计划准备后，**人类在自己的终端**执行返回的 service-approve 命令并输入 APPROVE。模型不应代替人类执行这条命令。`--yes` 仅供可信本地测试程序使用，MCP 不暴露它。

默认仅 mock 可执行。真实 provider 需在宿主启动参数中显式启用，例如 `mcp --brain codex --enable-codex`，随后还要逐计划人工审批。Claude 另外需要 `--enable-claude --claude-budget-ledger ...`，账本必须先经用户授权创建。MCP 参数不能修改这些开关、供应商路径或预算。

该服务支持 MCP `2025-11-25`、`2025-06-18` 的 stdio tools 子集，包含初始化、版本协商、ping、tools/list/call、结构化结果和工具错误。进度使用工具查询，不宣称实现全部 MCP 扩展。无 HTTP 监听端口。断开 stdin 时请求取消本会话任务，不自动重放；重启不接管旧任务，失效租约按原机制核对。

## 一键构建当前环境

构建机需要完整 Python 3.11+ 和网络。Windows 双击 `build.cmd`，或执行：

```powershell
.\build.ps1 --output 'D:\AI-Temp-Folder\agent-helm-build-new'
```

Linux/macOS：

```sh
./build.sh --output /tmp/agent-helm-build-new
```

输出目录必须是仓库外的新目录；不传 output 时创建独立临时构建目录。脚本创建私有 venv，校验 PyPI 发布的 pip wheel SHA256，使用固定 pip 25.3 与 PyInstaller 6.22.3。构建工具不进入运行时依赖，也不修改系统 Python。

构建完成后自动用真正的 stdio 客户端验证打包产物：握手 → 准备计划 → 未审批拦截 → 可信测试程序审批 → Mock 返工 → 独立测试 → 集成复测 → EOF 清理。然后生成：

- `dist/agent-helm/`：可执行文件及运行库。
- `release/agent-helm-版本-系统-架构.zip`：分发包。
- `release/SHA256SUMS`：校验摘要。
- 包内 `manifest.json`：版本、构建环境、文件哈希和验收标记。

采用 onedir，以保持 Windows 启动门与 Job Object 进程树监督的关系；不要只拷贝 exe。打包模式有专用的内部 fixture-check/process-host 入口，避免错误地把 exe 当作 Python 解释器调用。内置验证器的源文件作为数据携带，以继续检查其哈希。升级变更验证器代码时，旧审批仍会拒绝漂移，需要重新审批。

## Windows / Linux / macOS 分别构建

PyInstaller 按构建机系统及架构产出，不做跨系统编译。仓库的 **Actions → Native executable packages → Run workflow** 默认只验证 Windows。明确将 targets 选为 all，才启动 Windows x64、Ubuntu x64、Intel Mac 和 Apple Silicon Mac 原生构建。每个 runner 独立运行默认测试、构建及打包产物的 MCP smoke，通过后上传 14 天有效的 Actions artifact。当前按用户要求只推进 Windows 验收。

当前本地可验证 Windows 与 WSL/Linux；Mac 由相应 runner 验证。macOS 开发者签名/公证、Windows 代码签名与安装向导未配置。Git 与各产品原生 CLI 由使用环境单独安装；它们及账号凭据不会被打入包。离线 Mock 流程不需要产品 CLI 或 Git（除非选择 Git 夹具）。

Linux 产物使用构建机的系统 ABI，目标环境需兼容相应 glibc；可在较旧的目标 Linux 上原生构建。CI 使用 Ubuntu 22.04 构建 Linux 包。

## 验证边界

源码与 Windows/Linux 可执行包已通过离线 stdio 全流程，官方 MCP Python SDK 1.26.0 已验证 Windows 包的握手、工具发现与夹具调用。没有通过本轮 MCP 验证新增真实模型调用或收费 API 消耗。打包后的真实 provider 行为仍需逐项显式验收；此前 Claude 返工限制见 claude-worker-acceptance.md。

默认回归共 149 项：Windows 148 通过、1 项 symlink 权限跳过；WSL 149 项全部通过。构建日志、原始协议记录、SDK 测试环境和运行数据库均在仓库外。

官方依据：[MCP stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[Codex MCP 配置](https://learn.chatgpt.com/docs/extend/mcp)、[PyInstaller 原生构建](https://pyinstaller.org/en/stable/operating-mode.html)。
