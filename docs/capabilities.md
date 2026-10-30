# 能力与证据（2026-10-30，Asia/Shanghai）

| 阶段 | 已实现/验证 | 尚未满足的退出条件 |
|---|---|---|
| P0 | Codex、Qoder CN、Claude、Cursor CLI 路径/版本/help/认证状态探测；Windows 与 WSL Python/Git；官方文档复核 | Cursor 独立配置未登录；桌面产品安装不等于 CLI 可用；TRAE 按用户要求跳过 |
| P1 | Python 标准库 CLI 与本地 stdio MCP、严格契约、SQLite、DAG、Mock/Manual、事件、人工审批、幂等、异步 run、取消、有限返工 | MCP 最多 4 个并发 run、每 run 串行；无跨进程持久监督与供应商级全局熔断 |
| P2 | 独立快照、Git HEAD/index/status 基线、原目录保护、可信验收登记与哈希、文本/二进制/重命名/模式差异、审查门、集成重放 | 无 OS 级完整隔离；符号链接存在 TOCTOU 风险；仅以可信合成夹具验证，不保证任意业务代码兼容 |
| P3 | Windows Codex 0.164.0-alpha.11.1、Qoder CN 1.1.80 的真实候选与独立验证；Cursor 2026.10.22-27dcf5f worker 离线接入；Codex worker/reviewer 本机取消与 Job 归零已验证 | Cursor 未真实 smoke；没有实际供应商权限/额度故障注入、沙箱越界 canary 读写验证、远端 session 恢复、账单核实；Qoder 专项取消 smoke 未做 |
| P4 | CodexBrain 规划/新会话审查；交互主脑计划/审批；诊断先审查、候选哈希绑定、真实反馈返工、租约续期 | 无通用里程碑交互暂停/恢复；同供应商独立会话存在相关错误风险 |
| P5 | Codex 主脑 + Qoder CN worker，以及 Qoder CN 主脑 + Codex worker 两个方向均在合成 Git 仓库完成规划、诊断审查、故障注入返工、最终审查与集成复测 | 计费未核实，不能称订阅自动路由或生产就绪；Cursor 真实闭环待独立登录 |
| P6 | Claude CLI 原生 structured_output 三次实测通过；原始候选经哈希固定重放、独立测试/Codex 审查/集成复测通过；事务预算停止生效 | 故障返工链路因 verifier 上下文缺失未通过，修复仅做离线回归；重放不是新推理；实际模型 GLM；GUI 与 Windows↔WSL runner 仍关闭，TRAE 跳过 |
| P7 | SQLite backup API、维护锁、完整哈希清单与校验、同平台新目录恢复、恢复库 integrity_check 与已接受 run 复测；崩溃后禁止重放 | 无跨 schema 升级/降级迁移、远端 agent session 恢复、日志轮转/保留策略和全局监督服务 |

真实运行数据保存在仓库外的私有 state root，不提交账号信息、原始日志和带本机绝对路径的证据包。

首次真实 worker/reviewer 验证中，worker 首次修复成功；固定五用例通过；独立 Codex reviewer accept；集成候选复测通过。源码原空列表错误保留，供后续复现。具体 run ID 与原始证据保留于本地，不随公开仓库发布。

真实能力状态按字段分离：documented=true、installed=true、authenticated=true（CLI 明确显示 ChatGPT）、smoke_passed=本次夹具任务通过；billing_verified=false、production_ready=false。metadata probe 只探当前元数据，不沿用以前运行结果把 smoke_passed 自动变成 true。usage 来自供应商终态；订阅剩余额度和账单未读取。

完整真实规划链路中，计划由 CodexBrain 生成并通过本地审批入口登记；Codex worker、独立五用例、独立 Codex reviewer 均通过，随后集成副本复测通过。完整证据和规划 JSON 私下保留于仓库外。该实验没有合并业务代码或向业务仓库推送。

首版测试记录：Windows Python 3.12.14 运行 **40 项，39 通过、1 项因 symlink 权限跳过**；WSL Ubuntu Python 3.14.4 的 **40 项全部通过**。包含中文/空格路径、进程树终止、Mock 返工/额度/权限/超时、Manual 人工审查、幂等/租约/事务/恢复、备份副本复测、范围/哈希拒绝，以及候选 `exit(0)` 与导入副作用在执行前被 AST 拒绝。离线测试日志保留在仓库外，各使用本平台临时目录，没有共享打开 SQLite。

阶段 1 加固后测试为 62 项：Windows 61 通过、1 项因 symlink 权限跳过；WSL 62 项全部通过。真实 Windows Codex worker/reviewer 取消 smoke 分别确认启动宿主和 provider 子进程进入 Job，取消后 Job 活跃进程归零，状态 cancelled，没有 accepted 事件且源夹具未变。远端会话取消及额度结算未验证。

源码启动与两个平台的 unittest 已验证。未执行 pip 安装、构建 wheel、首次真实只读越界 canary、真实额度错误或远端 session 恢复测试，不能把相应的离线/监督器测试替代真实供应商验收。详见 phase1-acceptance.md。

阶段 2 扩展至 79 项离线测试（Windows 78 通过/1 权限跳过，WSL 79 通过）。另在自建脏 Git 仓库验证真实 Codex worker/reviewer，原索引与工作状态保持不变，候选集成复测通过。版本 2 的二进制、删除、重命名及 POSIX 模式操作通过 Manual/契约测试，尚未接入 Codex 自动生成；详见 phase2-acceptance.md。

官方依据：

- [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)：exec、JSONL、output-schema；实现同时对照本机 exec --help。
- [Codex 认证](https://learn.chatgpt.com/docs/auth)：ChatGPT 登录与 API key 认证不同。登录类型不能证明已核实账单或剩余额度。

阶段 3 接入 Qoder CN 调度器并新增 17 项测试，共 96 项；真实闭环和 Claude CLI 独立审查详见 [阶段 3 验收](phase3-acceptance.md)。Claude CLI 没有接入自动调度，TRAE 跳过，CodeBuddy、WorkBuddy 未接入。不对未核实账号权益或账单作推断。

v0.5 新增 Cursor 下游 worker 与 17 项离线测试，总计 113 项（Windows 112 通过/1 权限跳过，WSL 113 通过）。另完成 Qoder CN 主脑 → Codex 下游 worker 的真实两轮闭环；Cursor 已下载并验证版本，但独立配置未登录，尚未真实 smoke。详见 [worker 接入记录](worker-acceptance.md)。

v0.6 接入阶段新增 16 项 Claude/预算离线测试，合计 129 项，当时只探测元数据。随后 v0.6.1 的真实调用结果见下条与 [Claude worker 接入](claude-worker-acceptance.md)。Cursor 保持关闭，不再推进登录。

v0.6.1 在新增预算授权后实测 Claude 原生响应；故障返工暴露 verifier 约束没有传给模型，新增上下文传递修复与 5 项测试，总计 134 项（Windows 133 通过/1 权限跳过，WSL 134 通过）。原始正确候选重放通过，完整故障返工未通过，预算停止后未再收费验证。CLI 估算费用合计约 0.111436 美元，预留 0.30 美元全部用完；实际账单未知。

v0.7.0 新增 11 个本地 stdio MCP 工具、人工审批独立通道和 native onedir 打包。149 项默认测试：Windows 148 通过/1 权限跳过，WSL 149 通过。源码、Windows 可执行包、WSL/Linux 可执行包均通过真实 stdio 客户端的 Mock 全链路验证；官方 MCP Python SDK 1.26.0 验证 Windows 包的握手、工具发现与夹具调用。四平台 GitHub Actions 构建入口已配置，Mac 验收以对应 CI 结果为准。没有新增真实模型调用。详见 [MCP 与打包指南](mcp-packaging.md)。
