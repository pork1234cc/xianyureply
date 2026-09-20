# 管理命令契约

入口为项目根 scripts/bridge_control.py，所有命令支持 --instance 绝对路径，启停支持 --timeout（0 到 600 秒，默认 90）。已有根项目直接使用 src、config.yaml、.env、.venv、accounts、data、logs，不另建 runtime 或 instance.json，不更改正在运行进程的身份。

新建独立数据目录时，config.yaml、.env、.venv、instance.json 与持久数据在实例根，runtime 内保存唯一完整程序。只启动一个消息桥服务。登记在 LOCALAPPDATA/XianyuFeishuBridge/registry，仅保存非敏感路径和 ID。显式路径优先；默认依次选择脚本所属项目/实例、当前工作目录的已有项目、唯一登记实例。

| 命令 | 结果与副作用 |
| --- | --- |
| inspect | 只读工具路径、Python 版本和登记实例 |
| init | 校验包；已有根项目只检查原环境，新部署才安装；不覆盖已有配置 |
| doctor | 只读本地依赖、环境、配置存在性、绑定和数据库版本检查；不刷新凭据或迁移数据库 |
| start | 隐藏后台进程；已有真实进程返回 ALREADY_RUNNING；正常启动可能恢复登录及业务队列 |
| stop | 仅向有归属证据的实例写安全停止请求，等待主进程、记录的子进程和锁释放 |
| restart | 只有 stop 确认成功才启动 |
| status / diagnose | 同一脱敏摘要；不读取聊天正文、原始协议包、账号 UID 或错误原文 |
| upgrade | 独立目录先验证包和新环境，再停止、SQLite backup、备份配置账号、切换代码环境；保留旧副本。根源码目录拒绝自我覆盖并提示开发维护流程 |
| install-startup | 仅应用户自启请求执行；创建含实例 ID 的当前用户登录快捷方式，冲突不覆盖 |

stdout 单个 JSON：schema_version=1、operation、ok、code、instance_id、warnings、next_action。账号无进程证据时 accounts 为空；database_accounts 仅是旧数据库记录。queue 缺证据为 null，已查询时按实际状态返回字典，空字典代表没有任务。日志不由 diagnose 导出。

退出码：0 完成；2 配置/环境/登录缺项；3 不支持平台；4 锁冲突；5 超时；1 其他错误。PARTIAL_READY 是主循环活着但业务尚未全部就绪，应继续只读检查。启动失败或超时保留本机日志，不自动重发未知任务。

进程证据在 data/runtime-state.json，包含 PID、内核创建时间、实例归属、心跳和子进程。旧进程只有锁但没有新证据时报告 LOCKED，不按陈旧 PID 停止。STARTING 阶段同步尚未完成时 stop 要求等待主循环，不承诺强制停止。直接 CLI 仍有原退出清理行为；管理层不会新增强杀。

upgrade 首版仅接受无数据库或 schema 4；不提供推测式跨版本迁移。准备失败不停止旧实例；切换失败且新版未启动时恢复旧代码环境，实例保持停止。新版已经启动后不自动回滚数据库。备份保留配置、账号文件和 SQLite 一致性快照及哈希；含私密数据，只保存在实例内。

新独立实例自启使用 runtime/scripts/bridge_control.py，不再复制完整 management 包；旧版 upgrade 后保留 management/scripts/bridge_control.py 转发入口，兼容原快捷方式。根项目自启使用 scripts/bridge_control.py，移动/卸载根项目会影响该快捷方式。不自动启动比特客户端，请确保 API 已就绪。当前用户 Startup 方式无计划任务失败重启能力。不要将创建快捷方式称为已经完成真实注销登录验收。

根项目维护：直接修改 src，运行回归并重建 bundle-manifest.json。需要更新依赖时先安排停服，再用该项目 .venv 和 uv.lock 同步依赖；不执行独立实例 upgrade 来覆盖工作区。旧 PowerShell 启动脚本保留兼容已有自启，不需要为结构调整重装自启项。
