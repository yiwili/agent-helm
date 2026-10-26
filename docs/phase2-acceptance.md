# 阶段 2：Git 工作区与可信验证器

日期：2026-10-26（Asia/Shanghai）。所有实验均使用临时新建合成 Git 仓库；没有读取公司业务仓库、业务备份或账号配置作为实验数据。原始日志、HEAD/run ID 与本机路径保留在仓库外，不发布。

## 已实现

- 审批冻结源树、Git HEAD、分支、索引对象/模式、索引文件哈希，以及 porcelain 状态；提交前再次核对，启动与集成时再检测漂移。
- 独立副本只取 tracked 工作文件与未忽略的 untracked 文件，不携带原 `.git`。原索引、未提交变更、未跟踪/忽略文件不 stash/reset/清理。
- 结果版本 2：`write`（UTF-8/base64）、`delete`、`rename`；两端路径均受精确允许清单约束，路径不得重复或大小写冲突；执行位变更只在 POSIX 支持。重命名只做不覆盖目标的普通文件移动；重命名并改内容需以删除和写入表示。
- 证据保留文件哈希、大小、模式、文本差异、二进制标记及精确内容重命名识别。内容相同的删除/新增可能识别为 rename，此标记是展示用，实际集成依据基线/候选完整树哈希。
- 集成在新目录逐文件重放变化，先核对每个 task 的基线，再比对完整候选树哈希；最后运行各个已批准的固定验证器。不自动合并、提交或推送实验源仓库。
- 本地可信入口登记固定 argv、超时、可执行文件及外部脚本哈希；计划审批冻结 profile。脚本不能位于源工作区或可变 state root；修改后必须登记新 profile ID 并重新审批。
- 私有备份包含审批/profile 记录和 state root 内自建夹具的 Git 元数据；恢复后可复测。仓库外的验收脚本/可执行文件不复制，仍需原路径与原哈希。

## 验证

Windows 总计 79 项，78 通过、1 项 symlink 权限跳过；WSL 总计 79 项全部通过。新增 17 项覆盖脏索引/工作树/untracked/ignored 保留、审批与集成前 HEAD/index 漂移、原有删除、Git filter/include/特殊索引拒绝、版本 2 二进制与重命名、模式处理、固定 verifier 成败与篡改拒绝、Git 备份恢复。

真实 Windows Codex worker + 新会话 Codex reviewer 在新建脏 Git 夹具中完成修复并接受；独立固定测试和集成复测通过。原 HEAD、索引和工作区状态一致，源中的 ignored 文件未复制，副本无共享 `.git`。脚本首次在打印中文路径报告时遇到终端编码错误，报告已落盘且验证通过；终端输出修正为 UTF-8 后，用离线同入口复测通过，未为打印问题重复消费真实调用。

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
# 默认离线、新建合成 Git 仓库：
PYTHONPATH=src python scripts/verify_git_workspace.py --output NEW_PRIVATE_DIRECTORY
# 单独显式调用真实 Codex：
PYTHONPATH=src python scripts/verify_git_workspace.py --output NEW_PRIVATE_DIRECTORY --real-codex
```

## 仍有边界

只验证了自建合成仓库，不据此声称任何业务仓库兼容。当前不支持子模块、未合并索引、Git symlink、linked worktree、共享 Git 对象库、config includes/filters；没有运行 hooks、外部 diff、fetch 或生产操作。当前是内容快照，不复制原仓库历史，也不重建原 staged 内容；原索引和对象仍留在用户原仓库。

注册 argv 不意味着测试代码安全；自定义验证器会以当前本机权限运行，不能阻止恶意代码访问磁盘/网络。完整 OS 隔离、通用里程碑/真实返工、第二 provider、远端 session 恢复、计费核实仍属后续阶段。自动规划模板仍面向 stats 夹具；其他目标由交互主脑提供结构化计划和合适的可信验收程序。

数据库 schema 保持 1，源基线和 profile 冻结信息放入提交事件及私有审批文件；旧版仅 builtin 的 run 仍可读取。旧审批没有冻结信息时不能授权新注册的 verifier。
