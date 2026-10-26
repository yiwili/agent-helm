# 阶段 1：可靠性加固验收

日期：2026-10-26（Asia/Shanghai）。只使用本项目及临时新建合成夹具。公司业务代码、账号配置和业务备份没有作为实验输入；原始日志、run ID、进程 ID 与本机路径均只保留在私有证据目录。

| 验收项 | 级别 | 结果与边界 |
|---|---|---|
| 真实 Codex worker 取消 | Windows 真实 CLI | 通过；启动门确保 provider 进入 Job 后才获得 stdin；观察到宿主及 provider 两个进程，终止后活跃进程数为 0，cancelled、无 accepted、源夹具不变 |
| 真实 Codex reviewer 取消 | Windows 真实 CLI | 通过；Manual 合成候选先独立验证，再取消真实 reviewer；同样无错误接受 |
| 元数据阶段取消 | 离线 fake CLI + 真实进程监督 | 通过；不继续启动认证探测或 exec |
| 权限/额度/坏 JSON/缺终态 | 离线协议回放 + fake CLI | 通过；非零退出的权限/额度错误阻塞，缺终态、重复 JSON 字段、乱序 lifecycle 等拒绝；不是实际账号额度故障注入 |
| 调度器崩溃重启 | 真实本机调度进程 + Mock worker | 通过；强制结束调度进程，模拟租约到期，再打开状态库；needs_reconciliation，attempt 保持 1，不重放 |
| 审批运行中撤销 | 离线集成 | 通过；撤销请求取消，最终提交事务再次查审批；即使 reviewer 返回 accept 也不接受 |
| 审查提交事务故障 | 离线故障注入 | 通过；事件写入失败时，review、证据和状态整体回滚 |
| 停止不确定 | 离线故障注入 | 通过；Job 查询/停止不确定转 needs_reconciliation，不假称 cancelled 或 timed_out |
| 租约及 fencing | 离线双连接/事务 | 通过；过期检查和失效原子完成，失联 run 不继续派发独立任务 |
| stdin 阻塞时超时 | Windows/WSL 真实本机进程 | 通过；大输入不读也能执行超时回收 |
| 一致性备份 | SQLite backup API + 维护锁 | 通过；拒绝在途/不确定任务，阻止与集成并发，文件清单包含数据库与产物 |
| 备份篡改 | 离线故障注入 | 通过；产物、数据库、多余 WAL/文件、缺失内容等不匹配均拒绝 |
| 恢复演练 | 同平台新目录 | 通过；校验后复制、重定位内部路径、不覆盖、不自动执行；已接受候选集成复测通过 |

最终默认测试：Windows Python 3.12.14，62 项，61 通过/1 权限跳过；WSL Ubuntu Python 3.14.4，62 项全部通过。默认测试没有真实模型调用或认证变更。

复现：

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
# 以下独立命令显式调用真实 Codex，只在授权时运行：
PYTHONPATH=src python scripts/verify_codex_lifecycle.py --output NEW_PRIVATE_REPORT_DIRECTORY
```

本机停止确认不等于远端 turn 已终止或额度已结算。POSIX 进程组能停止组内进程，不能约束主动逃离进程组的恶意程序；Windows Job 也不是完整磁盘/网络沙箱。完整 OS 隔离、真实权限 canary、第二 provider、通用 Git 仓库和真实主脑返工仍在后续阶段。

数据库 schema 保持 1；遇到新 schema 在修改前拒绝，没有提供虚假的跨版本自动迁移。备份哈希用于检测损坏，不提供签名和不可信备份认证。
