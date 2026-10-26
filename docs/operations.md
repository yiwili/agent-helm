# 运行、取消、证据和恢复

## 状态文件

使用 CLI 全局 `--state-root`，或环境变量 `AGENT_ORCH_STATE`。Windows 默认 `%LOCALAPPDATA%\agent-helm`，Linux 默认 `$XDG_STATE_HOME/agent-helm` 或 `~/.local/state/agent-helm`。每个环境必须拥有独立目录和数据库。不要让 Windows / WSL / 网络共享同时打开同一库。

目录包括 state.db、runs、fixtures、integrations。所有随机 ID 由程序生成，源仓库不存运行数据。UTC 用于事件时间。events 的 seq 是全库游标，attempt_seq 是 run/task/attempt 内序号；event_id 相同但内容不同会被拒绝。

## 中断与恢复

1. `cancel RUN_ID` 记录取消请求。队列任务立即 cancelled；运行任务由监督器停止本次进程树后转 cancelled。
2. Ctrl+C 或进程崩溃时保留工作副本。不要以“CLI 返回了”推断供应商远端会话取消或未知副作用完成。
3. 租约到期后运行 `recover`，在途任务转 needs_reconciliation，不自动重跑。记录的 fence 不再接受旧终态。
4. 核对该 attempt 的目录、进程和外部副作用。首版不提供假装安全的“强制续跑”；保留旧 run，明确处理不确定状态后重新规划并审批新 run。
5. 明确 blocked（如额度/权限或缺人工结果）可用 `run RUN_ID --retry-blocked` 重新尝试；这会创建新 attempt，可能再次消费额度。needs_reconciliation 不允许该选项重试。

退出码：0 表示命令完成，run 是否接受以输出 state 为准；2 表示错误、阻塞、失败或超时；130 表示交互中断。interactive 的 reviewing 状态只是等待决策，非接受。

## 导出

`export RUN_ID --output NEW_DIRECTORY` 包含计划、审批 scope 引用、任务、事件、attempt 证据、diff、测试、review、候选快照及 SHA-256 清单，不包含凭据库。夹具 stdout 和文件内容也会进入包，导出前应人工检查。首版没有通用敏感数据识别能力，不能据此导出业务秘密。

## 一致性备份与恢复

1. 停止新派发，取消/完成在途任务，并停止其他进程执行集成或导出写操作。
2. `backup --output NEW_DIRECTORY` 拒绝 running/verifying/reviewing/cancel_requested。在 SQLite 写锁下用官方 backup API 备份数据库，并复制 runs/fixtures/integrations。不直接复制运行中的主数据库而丢掉 WAL。
3. 保留原 state root 的完整目录（包括 WAL/SHM）作为回退。恢复时先停止所有使用该 state root 的进程。
4. 首版工作目录和 verification report 记录绝对路径；正式恢复请将备份恢复到原绝对路径。先把旧目录改名保留，再用备份目录重建原路径，不混合两个版本的数据库/WAL。
5. 用 Python sqlite3 执行 `PRAGMA integrity_check`，检查 schema_migrations 为 1；逐一 `status`、`events`、`review --show-evidence`。已接受候选必须核对哈希并在独立副本重跑 `integrate`。
6. 备份恢复测试在临时位置打开副本，验证完整性、状态与候选哈希并复测；源夹具仍需存在。不能据此声称支持任意路径迁移或 schema 升降级。

## 升级和代码回退

停派发 → 保留当前程序与完整备份 → 新版本离线契约回放 → 自建夹具 → 显式真实 smoke → 再开放。Codex CLI 版本漂移会拒绝调用，先核对新 help 和协议再改版本 pin。当前没有数据库迁移支持，遇到 schema 变化先保持旧程序及旧库成套回退。

未集成的候选无需改原项目，保留或弃用副本即可。本程序不自动合并/推送/发布，也不执行 reset --hard、stash 或清理用户文件。首版不自动删除失败产物；手工清理前必须停止进程、核对目标确在 state root 内，并保留所需备份。
