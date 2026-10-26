# 交付复核

日期：2026-10-26（Asia/Shanghai）。工作目录原为空目录；只用本项目与自建合成夹具，未改动其他项目、登录配置或生产环境。公司业务代码不得作为实验输入。

运行依赖为 Python 标准库，没有安装新软件。Windows 使用已有 Codex runtime Python 3.12.14；WSL Ubuntu 使用 Python 3.14.4。Codex `--version`、`exec --help`、`login status` 只读探测后实现精确版本适配。

| 验证 | 结果 |
|---|---|
| Windows unittest | 40 项，39 通过，symlink 创建权限不足跳过 1 项 |
| WSL unittest | 40 项全部通过，包括 symlink 拒绝 |
| Windows Job Object / Linux 进程组 | 超时后子进程未能进行延迟写入；stdout 限制与 literal argv 测试通过 |
| 独立测试门 | 错误实现不能被自报成功接受；两轮返工后升级人工；固定验证入口不能由返回文件改写 |
| 纯夹具检查 | 候选导入/文件写入/提前退出，在执行前被固定 AST 语法白名单拒绝 |
| 状态恢复 | 过期租约进入 needs_reconciliation，没有重复 attempt；旧 fence 与迟到终态拒绝 |
| 一致性备份恢复 | 官方 SQLite backup API；恢复副本 integrity_check=ok；已接受 run 在副本中保持状态并通过集成复测 |
| 实际 Codex | 固定版本、ChatGPT 登录；规划、worker、独立 reviewer、集成复测通过 |
| 原夹具保护 | 源 `stats.py` 仍保留 None 错误；修复只在 candidate/integration 副本 |

测试命令：

```powershell
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests -v
```

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

真实验收摘要见 capabilities.md。测试日志、运行 ID 和失败尝试保留在仓库外的本地证据目录。头两次真实试运行因 CLI 参数位置错误而失败，未把失败标成成功，事件保留；调整为 `codex exec --ignore-user-config ...` 后通过。除元数据和明确的合成夹具调用外，没有批量供应商探测、API 回退或真实业务任务。

WSL 启动时报告宿主 localhost proxy 未映射警告；离线测试不依赖网络，因此该警告不影响其通过，也不证明 WSL 可以访问真实模型服务。

局限与未验证项以 capabilities.md 为准；本次交付不能标为 production_ready。
