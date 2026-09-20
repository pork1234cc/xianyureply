---
name: xianyu-feishu-bridge
description: 部署、配置、启动、停止和排查闲鱼与飞书消息桥；用于多账号消息转发及本人通过飞书回复文字和图片。
license: Apache-2.0
compatibility: 需要能在 Windows 本机执行 PowerShell 和 Python 的 Agent；依赖 Python 3.11+、uv、Node.js、网络及飞书和闲鱼账号配置。
metadata:
  version: "0.2.0"
---

# 闲鱼飞书消息桥

整个项目就是本 Skill，程序在根目录 [src](src)，管理入口在 [scripts](scripts)，只维护一份业务源码。提供闲鱼与飞书消息桥的部署与管理；账号和客户由既有绑定及直接引用映射决定，不新增 AI 自动回复。

## 定位与执行

从本文件位置确定 Skill 根目录，使用 [scripts/bridge_control.py](scripts/bridge_control.py) 的绝对路径。先识别当前项目已有 Python 环境；无环境的新部署可使用已安装的 Python 3.11+ 引导，init 会创建实例专用环境，不向全局安装依赖。

```powershell
# 替换为实际绝对路径；命令可在任意工作目录执行。
& $Python "$SkillRoot/scripts/bridge_control.py" inspect
& $Python "$SkillRoot/scripts/bridge_control.py" init --instance $InstanceRoot
& "$InstanceRoot/.venv/Scripts/python.exe" "$SkillRoot/scripts/bridge_control.py" doctor --instance $InstanceRoot
```

已有项目优先直接管理：根目录已有 config.yaml 和 .venv 时，status/doctor/start 等无需 --instance，不另建实例、不复制账号。显式 --instance 优先；其次为脚本所属的已有项目/运行实例、当前工作目录中的已有项目、唯一登记实例。多个候选无法确定时再询问。inspect 列出登记实例，未登记的原项目仍可按路径管理。

新部署只需一个实例：可在根 Skill 目录 init，也可把持久数据放在用户指定目录（未指定时建议 `$env:LOCALAPPDATA/XianyuFeishuBridge/instances/default`，调用 init 时显式传入）。安装目录可能被安装器更新覆盖时，优先使用独立数据目录；这是同一个消息桥的存储位置，不要求同时运行两套服务。禁止复制现有凭据创建第二个在线实例。

## 按任务执行

- 安装 Skill：仅安装整个项目的公开资源，不执行 init/start。发布资源由根目录 [bundle-manifest.json](bundle-manifest.json) 校验。本地安装先通过 build_skill_bundle.py --output 导出干净目录，不直接安装含账号和环境的工作目录。
- 新部署：读 [docs/skill-setup.md](docs/skill-setup.md)，执行 inspect、init，补齐本地配置，复用原 CLI 完成人工登录及绑定，然后 doctor/start。
- 启停、状态、升级或自启：读 [docs/skill-operations.md](docs/skill-operations.md)。启动已有实例不默认注册自启。用户要求自启时才调用 install-startup。
- 图片失败、登录异常、队列异常：读 [docs/skill-troubleshooting.md](docs/skill-troubleshooting.md)，先 status/diagnose，不向客户发送探测消息。
- 宿主支持与验收边界：读 [docs/skill-compatibility.md](docs/skill-compatibility.md)。无 Windows 命令能力时报告限制，不宣称本机运行成功。
- 修改程序：直接修改 src，按 tests 验证，重建根清单；不要另建内层 Skill 或复制业务源码。开发方式见 [README.md](README.md)。

## 结果与业务边界

管理命令 stdout 为单个 JSON，stderr 仅给脱敏提示；以 ok、code、进程身份和心跳判断。RUNNING 表示主循环、账号及飞书就绪证据，不表示买家已经收件。PARTIAL_READY 需说明未就绪部分；TIMEOUT 后先查状态，不能连续重启。数据库 database_accounts 是历史业务证据，不能替代 process。

Cookie、Secret、聊天正文不回显到对话。缺少配置时列出字段与本地保存位置；人工登录的 UID 核对由用户完成，不能代填。账号同步、飞书绑定会访问本人配置的平台，遵循用户任务范围执行。

客户文字与 PNG/JPG/JPEG 图片沿用原队列、同账号五秒间隔及固定 parent_id 路由；不猜最近客户、不另开正式发送连接，UNKNOWN 不自动重发。真实发送测试必须有用户明确指定的测试会话。

更新 Skill 不等于升级运行实例；upgrade 保留旧代码、环境及一致性数据库备份，不自动回滚队列。删除实例、账号、数据或启动项属于单独的危险操作，应先说明具体目标和后果并取得确认。
