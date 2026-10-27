# 能力与证据（2026-10-27，Asia/Shanghai）

| 阶段 | 已实现/验证 | 尚未满足的退出条件 |
|---|---|---|
| P0 | Codex、Qoder CN、Claude CLI 路径/版本/help/认证探测；Windows 与 WSL Python/Git；官方文档复核 | 桌面产品安装不等于 CLI 可用；TRAE 按用户要求跳过 |
| P1 | Python 标准库 CLI、严格契约、SQLite 表、DAG、Mock/Manual、事件、审批、幂等、状态转换、有限返工 | 无常驻调度服务、全局并发与供应商动态熔断服务；当前每 run 串行 |
| P2 | 独立快照、Git HEAD/index/status 基线、原目录保护、可信验收登记与哈希、文本/二进制/重命名/模式差异、审查门、集成重放 | 无 OS 级完整隔离；符号链接存在 TOCTOU 风险；仅以可信合成夹具验证，不保证任意业务代码兼容 |
| P3 | Windows Codex 0.164.0-alpha.11.1、Qoder CN 1.1.80；真实只读 JSON 候选、独立验证；Codex worker/reviewer 本机取消与 Job 归零已验证 | 没有实际供应商权限/额度故障注入、沙箱越界 canary 读写验证、远端 session 恢复、账单核实；Qoder 专项取消 smoke 未做 |
| P4 | CodexBrain 规划/新会话审查；交互主脑计划/审批；诊断先审查、候选哈希绑定、真实反馈返工、租约续期 | 无通用里程碑交互暂停/恢复；同供应商独立会话存在相关错误风险 |
| P5 | Codex 主脑 + Qoder CN worker 在新建合成 Git 仓库完成规划、诊断审查、故障注入返工、最终审查与集成复测 | 计费未核实，不能称订阅自动路由或生产就绪；未真实验收交换角色方向 |
| P6 | Claude CLI 经 CC Switch 自定义 API 的连通性通过；按次/总额预留；结构化审查被契约拒绝 | 未完成 Claude 审查接入；不是自动 API fallback；实际模型 GLM；GUI 与 Windows↔WSL runner 仍关闭，TRAE 跳过 |
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
