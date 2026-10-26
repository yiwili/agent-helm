# 运行、取消、证据和恢复

## 状态文件

使用 CLI 全局 `--state-root`，或环境变量 `AGENT_ORCH_STATE`。Windows 默认 `%LOCALAPPDATA%\agent-helm`，Linux 默认 `$XDG_STATE_HOME/agent-helm` 或 `~/.local/state/agent-helm`。每个环境必须拥有独立目录和数据库。不要让 Windows / WSL / 网络共享同时打开同一库。

目录包括 state.db、runs、fixtures、integrations。所有随机 ID 由程序生成，源仓库不存运行数据。UTC 用于事件时间。events 的 seq 是全库游标，attempt_seq 是 run/task/attempt 内序号；event_id 相同但内容不同会被拒绝。

## 中断与恢复

1. `cancel RUN_ID` 记录取消请求。队列任务立即 cancelled；运行任务由监督器停止本次进程树后转 cancelled。
2. Ctrl+C 或进程崩溃时保留工作副本。不要以“CLI 返回了”推断供应商远端会话取消或未知副作用完成。
3. 租约到期后运行 `recover`，在同一事务内核对到期状态、转 needs_reconciliation 并使旧 fence 失效，不自动重跑。同一 run 的其他任务也不会继续派发。
4. 核对该 attempt 的目录、进程和外部副作用。首版不提供假装安全的“强制续跑”；保留旧 run，明确处理不确定状态后重新规划并审批新 run。
5. 明确 blocked（如额度/权限或缺人工结果）可用 `run RUN_ID --retry-blocked` 重新尝试；这会创建新 attempt，可能再次消费额度。needs_reconciliation 不允许该选项重试。
6. `revoke SCOPE_ID` 原子撤销审批并请求取消相关在途任务；取消轮询覆盖元数据探测、worker 和 reviewer。最终接受事务再次核验审批、租约、证据和 review，撤销后的 accept 不能落库。停止无法确认时保持 needs_reconciliation。

退出码：0 表示命令完成，run 是否接受以输出 state 为准；2 表示错误、阻塞、失败或超时；130 表示交互中断。interactive 的 reviewing 状态只是等待决策，非接受。

## 导出

`export RUN_ID --output NEW_DIRECTORY` 包含计划、审批 scope 引用、任务、事件、attempt 证据、diff、测试、review、候选快照及 SHA-256 清单，不包含凭据库。夹具 stdout 和文件内容也会进入包，导出前应人工检查。首版没有通用敏感数据识别能力，不能据此导出业务秘密。

## 一致性备份与恢复

1. 停止新派发，取消/完成在途任务，并停止其他进程执行集成或导出写操作。
2. `backup --output NEW_DIRECTORY` 拒绝 running/verifying/reviewing/cancel_requested/needs_reconciliation。与集成操作共用跨进程维护锁，在 SQLite 写锁下用官方 backup API 备份数据库，并复制 runs/fixtures/integrations。写入完整 SHA-256/大小清单，检查复制期间变化及 SQLite 完整性；不直接复制运行中的主数据库而丢掉 WAL。
3. 保留原 state root 的完整目录（包括 WAL/SHM）作为回退。恢复时先停止所有使用该 state root 的进程。
4. 运行 `verify-backup --backup BACKUP_DIRECTORY`。文件缺失、修改、多余文件、非法路径、symlink/junction、数据库不完整或版本不支持均拒绝。清单用于发现损坏，不是来源认证；只恢复自己信任的备份。
5. `restore --backup BACKUP_DIRECTORY --output NEW_STATE_ROOT` 只写入不存在的新目录，先验证再复制。支持同平台重定位 state root 内部路径，重算测试报告哈希，并保留审批/事件/候选对象。外部 source 路径保持原样，仍需原目录。不会执行任何任务或改写原 state root。
6. 逐一 `status`、`events`、`review --show-evidence`，核对候选哈希，然后在独立副本运行 `integrate` 复测。恢复结果显式记录 replayed_tasks=0。
7. v0.1 旧备份没有完整清单，v0.2 的恢复入口拒绝；保留旧程序并按其原路径恢复规则人工处理，不混合数据库/WAL。当前不支持跨 OS 恢复或 schema 升降级。恢复失败会保留目标目录供检查，原数据不被删除或覆盖。

## 升级和代码回退

停派发 → 保留当前程序与完整备份 → 新版本离线契约回放 → 自建夹具 → 显式真实 smoke → 再开放。Codex CLI 版本漂移会拒绝调用，先核对新 help 和协议再改版本 pin。当前没有数据库迁移支持，遇到 schema 变化先保持旧程序及旧库成套回退。

未集成的候选无需改原项目，保留或弃用副本即可。本程序不自动合并/推送/发布，也不执行 reset --hard、stash 或清理用户文件。首版不自动删除失败产物；手工清理前必须停止进程、核对目标确在 state root 内，并保留所需备份。
