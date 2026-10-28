# 下游 worker 接入记录（2026-10-28）

本轮仅操作 Agent Helm 与新建无敏感合成夹具。日志、下载工具、独立认证配置、数据库、原始响应与证据均在仓库之外。未使用公司业务代码、备份或凭据，没有调用 Claude 收费 API 或操作 TRAE 桌面。

| 产品 | 主脑 | 下游 worker | 当前证据 |
|---|---|---|---|
| Codex CLI | 支持 | 支持 | 已有真实 worker/reviewer 证据；本轮另验收 Qoder 主脑下的 worker 角色 |
| Qoder CN CLI | 支持 | 支持 | 已有 Codex 主脑下的真实 worker 证据 |
| Cursor CLI | 未接入 | 支持，默认关闭 | 官方包版本与未登录状态已实测，离线契约通过；真实推理尚未执行 |

## Cursor 实现边界

下载官方 Windows 包 `2026.10.22-27dcf5f`，检查归档成员路径与符号链接后展开到私有工具目录，直接用包内原生 Node 执行 index.js。未运行远程安装脚本，没有修改全局 PATH。版本 pin 与输出格式依据本机 help 及下列官方文档；本机只完成元数据探测，没有将下载或解析器测试当作真实 smoke。

必须显式准备独立配置目录并在其中通过官方 CLI 登录。配置拒绝工具，禁止加载自定义端点、MCP、hooks、plugins、rules、skills；不能复用工作副本 `.cursor` 配置。进程环境只允许受控的绝对 `CURSOR_CONFIG_DIR` 覆盖，不接受 CURSOR_API_KEY、CURSOR_API_ENDPOINT、NODE_OPTIONS 或任意环境注入。Cursor 的 user profile 没有被替换，认证信息不从其他配置中提取或复制。

候选仍由 orchestrator 按允许路径应用，worker 不获得测试命令选择权。stream-json 必须从官方 login 初始化开始、绑定同一 session、没有 tool_call、以成功 result 结束，内容必须为精确 JSON。权限拒绝、异常事件、截断、非零退出、API key 来源或版本变化都不能通过。ask/sandbox 及工具 deny 配置不等于完整 OS 隔离，真实权限 canary 与 Cursor 取消行为尚未验收。

Cursor 未登录时阻断真实调用，主脑不自动换 provider，不回退收费 API。后续登录后应显式运行 `scripts/verify_cross_provider.py --brain codex --worker cursor`，才可以更新 smoke 状态。

## 测试

新增 17 项离线测试覆盖完整终态、session 和版本漂移、API key 来源拒绝、工具事件拒绝、JSON 围栏/重复键/截断拒绝、独立配置与扩展拒绝、受控环境、默认关闭、CLI 选择与 Codex/Cursor 下游角色。所有默认测试使用本地 fake runner，不启动真实产品或消费额度。

本轮真实流程：Qoder CN 1.1.80 生成计划并担任独立主脑，Codex CLI 0.164.0-alpha.11.1 担任下游 worker。实现前诊断审查通过；可信测试程序故意破坏首轮候选，审查 rework 后第二轮修复 accept；固定测试和集成复测均通过。原仓库 HEAD、索引与工作区保持不变，候选没有共享 `.git`。错误注入不代表 Codex 模型失败，模型原始响应另外私下保留。

Cursor 的真实状态为 installed=true、compatible=true、authenticated=false、smoke_passed=false，因缺少登录未进行任何推理。本轮没有新增收费 API 调用或改动现有产品登录。原始私有证据不提交公开仓库。

全量回归共 113 项：Windows 112 项通过、1 项因 symlink 权限跳过；WSL 113 项全部通过。补充递归读写 deny 规则后，17 项 Cursor 契约测试再次通过。真实 Cursor 权限效果仍需登录后另验收，不能用离线配置检查代替。

官方依据：

- [CLI 参数](https://cursor.com/docs/cli/reference/parameters)
- [输出格式](https://cursor.com/docs/cli/reference/output-format)
- [权限配置](https://cursor.com/docs/cli/reference/permissions)
- [独立配置目录](https://cursor.com/docs/cli/reference/configuration)
