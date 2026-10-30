# Tauri 桌面工作台（Windows）

v0.8.1 修复 Windows 盘符绝对路径被 Tauri 当作 URL、导致安装后 `ERR_FILE_NOT_FOUND` 的问题。构建改用相对目录，跨盘使用 Windows 文件命名空间路径；发布构建通过 Tauri 自身的类型解析检查目录与入口文件。打包 CI 另用 `--check-embedded-assets` 只读检查可执行文件内的 HTML/JS/CSS，不创建窗口、启动后端或调用模型；这不是图形界面的运行验收。

桌面界面和 MCP 共用 Python 调度、候选哈希及独立验收逻辑。桌面端另有仅供本地用户的私有通信入口，用于配置、审批、预算确认和人工审查；这些权限不会加入 MCP 工具。

## 使用界面

- **任务工作台**：创建全新合成夹具 → 查看冻结计划、模型和连接 → 明确审批 → 执行 → 查看 diff、测试和进度 → 人工审查 → 集成至新合成副本。不会写回公司仓库、合并或推送。
- **子 Agent**：Mock、Codex、Claude CLI、Qoder CN。真实 Worker 默认关闭。可选择已有模型、增加准确模型 ID，或留空使用 CLI 默认值。实际调用向 CLI 传入 `--model`；请求模型不等于上游实际模型或账单证明。Codex 保留原有隔离用户配置和 ChatGPT 登录限定；Claude 沿用本机 CLI / CC Switch 配置。
- **连接**：自动发现本机 CLI，或输入原生 CLI 的绝对路径。不能填 shell 命令或 `.cmd/.bat/.ps1`；当前不支持通用 API/MCP 连接当作 Worker。模型及路径在计划创建时冻结，后续配置更改不影响已有计划和返工。
- **Claude 预算**：由用户在界面明确授权新的外部账本；不会复用历史已耗尽授权。每次推理先预留，失败不自动退款；本地预留不是代理账单硬上限。
- 连接检查仅检查版本与认证状态，不发起模型推理。版本兼容性仍受项目原有固定 CLI 协议限制。
- 浏览器打开开发页面仅显示未连接预览，执行按钮禁用；必须通过 Tauri 才能访问桌面服务。

## 开发启动

需要 Node.js 24、Python 3.11+、Rust stable MSVC toolchain、Visual Studio C++ Build Tools（含 Windows SDK），以及 WebView2。按照 [Tauri Windows 官方前置条件](https://v2.tauri.app/start/prerequisites/) 安装。

在仓库根目录的 PowerShell 中：

```powershell
$env:AGENT_HELM_PYTHON = (Get-Command python.exe).Source # 必须是真实 Python，不能是 Microsoft Store 占位程序
Set-Location desktop
npm ci
npm run dev
```

构建文件、Cargo target、前端 dist 和生成配置默认进入系统临时目录；可设 `AGENT_HELM_DESKTOP_OUTPUT` 为仓库外的新输出目录。运行状态保存在 Windows 用户的应用本地数据目录下 `io.github.yiwili.agenthelm/state`，不进入仓库。界面关闭时先关闭后端输入，等待任务取消，再回收子进程；异常或丢失响应不自动重试，需人工核对状态。

## 一键构建 Windows 安装程序

在已安装上述前置依赖的 Windows 上，双击 **`desktop/build.cmd`**。它安装锁定的前端依赖，调用 PyInstaller 生成完整 **onedir** 后端，再由 Tauri 打包为 NSIS 安装程序。后端 `_internal` 与主程序一并作为资源复制，不使用 onefile，保留 Windows 子进程监督方式。也可预先设置 `AGENT_HELM_ENGINE_DIR` 指向**基于当前源码新构建的** onedir 后端目录，以跳过后端构建。

本构建入口不会启动或验证打包后的程序；`scripts/build.py --build-only` 的后端也明确标记为未验证输入，不生成“已验收”发行摘要。安装包通常位于输出目录 `cargo/release/bundle/nsis`。前端和 Rust 的开发检查不等于安装包可用性验收。

当前只推进 Windows，Linux/macOS 验证延期。原有命令行/MCP 打包验收工作流改为手动触发，避免本次桌面提交自动运行产物测试。新增 Windows 桌面 CI 仅执行离线 Python 测试、TypeScript/DOM 测试、静态前端构建和 `cargo check --locked`，不生成/启动安装包，不调用真实模型。

也可在 **Actions → Windows desktop development checks → Run workflow** 勾选 `bundle`，构建 Windows 安装程序、只读自检内嵌文件并上传 14 天有效的 artifact；该分支不打开图形窗口、不进行安装后功能验收，也不调用任何模型。默认不勾选，继续只跑开发检查。
