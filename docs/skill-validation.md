# 标准 Skill 本地实施与验收记录

## 根 Skill 真实收发确认与发布（2026-09-20）

根目录调整完成后，用户明确反馈“测试可成功发送图片和文本”，确认本轮文字与图片实际发送可用。未单独确认图片编码及全部账号的逐一收发，不扩展为所有图片格式、所有账号均完成真人收件测试。

用户随后授权提交推送，再安全重启原项目服务并核对所有闲鱼账号在线。发布前查询原实例为 RUNNING，A1/A2/A3 均 ONLINE、飞书在线，没有 QUEUED/DISPATCHING 任务。重启使用本项目 scripts/bridge_control.py，沿用现有配置、账号和数据；不创建第二个正式实例。下方“未推送/未重启”描述属于此前结构调整阶段记录。

## 根目录 Skill 0.2.0（2026-09-20）

整个仓库作为 Skill，唯一根 SKILL.md、唯一根 src；管理脚本归入 scripts，四份参考文档归入 docs。旧嵌套封装已备份至仓库外，不再作为发布输入。已有项目通过根管理入口直接管理，不创建第二个正式实例。完整调整见 [根 Skill 方案](../ROOT_SKILL_PLAN.md)。

本轮结果：

- 全量回归最终 426 项通过（包含根布局 13 项），Ruff、依赖锁、差异空白、文档相对链接和发布资源敏感标识检查通过；仅既有飞书 SDK datetime 弃用提示。
- 51 个业务源码文件与调整前字节一致；本轮只调整包装、管理、文档和测试。为稳定跨平台发布哈希统一公开文本 LF。
- 隔离 `npx skills@1.7.0 add` 仅发现一个 Skill，Codex 和 Claude Code 复制安装的根入口、源码、管理脚本及资源均通过 strict 校验。官方 skills-ref 对规范命名的导出/安装目录校验通过。开发仓库名 xianyureply 不需更改；直接用旧 validator 校验任意开发文件夹名不作为安装结论。
- 安装产物在任意工作目录运行 inspect/init/status/doctor，建立自己的 .venv 并从其 site-packages 加载程序。空实例 STOPPED、预检 CONFIG_REQUIRED，没有借用原项目凭据或发消息。
- 以备份的 0.1.0 程序新建空旧实例，再执行根布局 upgrade，实际 UPGRADED。新旧管理入口均返回同一实例 STOPPED；只有 runtime 保留完整程序，旧 management 仅转发，升级备份完整保留。
- 正式项目根管理入口实测 RUNNING，A1/A2/A3 与飞书在线；没有重启正式进程或迁移其数据。
- 本机 skill-creator 的 quick_validate.py 仍不接受 compatibility，记录为旧校验器限制，未改动标准字段；官方参考校验通过。

测试产物留在仓库外 `E:/2-AI/xianyureply-skill-check-6422deba`，旧封装备份在 `E:/2-AI/xianyureply-root-skill-backup-20260920-092833`。本轮未推送远端，未重做真人收发、真实注销登录自启或其他宿主长驻验证。下方 0.1.0 的 NPX 哈希、路径、命令及宿主记录均是旧布局历史证据。

当前维护命令（在项目已有 .venv 下）：

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe scripts/build_skill_bundle.py
.\.venv\Scripts\python.exe scripts/verify_skill_bundle.py
.\.venv\Scripts\python.exe -m ruff check src tests scripts/bridge_control.py scripts/bundle.py scripts/build_skill_bundle.py scripts/verify_skill_bundle.py
.\.venv\Scripts\python.exe -m pytest -q
```

## 0.1.0 历史实施与验收

日期：2026-09-20。版本：0.1.0。业务基线：9092ac43779e2aaf0d56227bee665dd30a7ebf61；新增封装代码以工作区及 bundle-manifest.json 的逐文件 SHA-256 为准。

## 实施范围

已实现唯一 skills/xianyu-feishu-bridge/SKILL.md、四份按需参考、自包含运行资源、构建及校验脚本、十个管理命令、独立实例环境、登记、原子心跳、Windows 进程身份和双层锁、SQLite 一致性备份及受控升级。管理层不修改客户路由、回复间隔或 UNKNOWN 规则。

生成物通过 scripts/build_skill_bundle.py 更新，scripts/verify_skill_bundle.py --source-check 核对根源码。静态 JS、Pillow 依赖、uv.lock、通用账号模板、LICENSE 和 NOTICE 都包含在包中。未选用可选 openai.yaml，避免使通用能力依赖宿主展示元数据。

`.gitattributes` 固定发布白名单与 Skill 文本为 LF，避免 Windows Git 换行转换使哈希失效。正式 SHA-256 对实际文件字节计算，不使用忽略换行差异的弱校验。

## 已取得的证据

| 项目 | 结果 |
| --- | --- |
| 原有基线 | 393 项离线测试通过；仅既有飞书 SDK datetime 弃用提示 |
| 最终回归 | 410 项离线测试通过；改动 Python 及完整 src/tests 的 Ruff 通过 |
| Windows 进程 | 内核 PID+创建时间、PID 复用、过期心跳、实例 ID 冲突、锁冲突、双 start、安全 stop 回归通过 |
| 状态契约 | 账号或飞书未就绪返回 PARTIAL_READY；数据库旧 ONLINE 不替代真实进程 |
| 路径与环境 | 中文空格实例、绝对路径、任意 cwd、独立 .venv、其他环境不静默替换已验证 |
| 初始化 | 实际 uv --locked 安装成功，重复 init 返回 ALREADY_INITIALIZED；缺凭据 doctor 返回 CONFIG_REQUIRED，队列为 null |
| 包格式 | skills-ref 0.1.0（官方源码提交 69ef37e9424c0a7ea9dd2293b559e43ec8176379）校验通过 |
| NPX | skills 1.7.0，隔离项目目录，codex 和 claude-code 复制模式安装成功，只发现一个 Skill；安装产物哈希校验通过 |
| 安装独立性 | 实例以 --no-editable 安装，goofish_bridge 从实例 .venv/Lib/site-packages 加载；未引用维护仓库环境 |
| 安装产物后台模拟 | 独立启动命令退出后另一次命令查询 RUNNING，双 start 返回 ALREADY_RUNNING，stop 返回 STOPPED；模拟账号状态，不访问真实平台 |
| Claude Code | 2.1.274，实际读取 .claude/skills 中入口，调用实例 status 返回 STOPPED；无权限拒绝、无消息发送 |
| 当前 Codex 会话 | 实际读取 .agents/skills 中安装入口，调用同一 status/doctor/init，返回实际 JSON |
| 独立 Codex CLI | 0.155.1，read-only 沙箱启动被 helper_sandbox_lock_failed / SetNamedSecurityInfoW 错误 5 阻止；未绕过，不能计为通过 |
| 升级 | 隔离实例实际升级返回 UPGRADED，旧 runtime、.venv、管理包及快照保留；准备失败和切换失败的回归通过 |
| SQLite | 测试包含未 checkpoint 的 WAL 数据，一致性备份可读回；失败恢复不回滚队列 |
| PowerShell 引导 | 5.1 实际执行回归通过；中文脚本使用 UTF-8 BOM，避免被本地编码误解析 |
| 自启注册 | 仅在临时 APPDATA 的非真实 Startup 目录创建快捷方式，目标为实例 pythonw.exe；重复注册返回 LOCKED，原 Windows 自启配置未改变 |

最终安装产物再次完成双 Agent NPX 复制安装与标准校验。发布清单 SHA-256：`d548cc0db0fcf17f5196425e3dea04eab2e672bedb5e81294faf8203cdf88aed`。安装器给出的整个 Skill 内容哈希为 `b38afaf0504d62e566a9516b8b5adc4616a472299ba69e10b1e23f18e2cf7834`，两者哈希对象不同。

最终独立实例位于系统临时目录 `xianyu-skill-release-check-218f5e77626145eda26a3c495f9aae1e/最终实例 中文空格`；本地验收时不含真实账号，后续真实验收已加入私有账号配置，不能作为发布资产。首次安装在较长的隔离 uv 缓存目录构建第三方 pyexecjs 失败；使用较短的 `UV_CACHE_DIR` 后，原实例重复 init 成功，未覆盖模板。这提示长缓存路径的构建兼容问题，不能据此宣称任意长度的 Windows 路径都支持。未删除失败缓存或安装备份。

本机 skill-creator 附带 quick_validate.py 已执行，但其允许字段清单缺少标准 compatibility，不能通过。依据 Agent Skills 标准，保留合法字段，改用官方参考实现校验；没有为通过旧校验器而删掉平台边界。

## 未完成的发布门槛

- 独立 Codex CLI 沙箱问题修复后重测；当前会话执行成功不替代该客户端记录。
- 在操作系统层彻底禁止访问维护仓库的测试环境验收；目前确认独立安装位置及无编辑安装回指，不等于 OS 访问隔离。
- 真正关闭 Agent 应用/注销 Windows 后的服务存活与登录自启；命令行父进程结束测试不能外推为所有宿主行为。
- 链接安装模式，以及 Cursor、OpenCode、Hermes 等客户端发现和调用。
- 文字真实发送已取得服务端接受证据，用户已确认买家收到编号 20260920-023451 的文字，文字闭环通过；后续手动单张图片闭环也已通过；本次图片具体编码未单独确认，PNG/JPG/JPEG 各格式及异常边界仍需分别记录。
- 新版只接受 schema 4 或尚未建库的实例，其他 schema 的迁移需单独设计；不承诺任意未来版本升级。
- 旧项目登记/迁移没有自动入口；旧进程缺少新证据时仅报告锁冲突。本轮真实验收临时停止并恢复正式项目，没有迁移或回滚其数据库。
- 尚未提交、推送或发布。远端安装命令只有远端包含本次生成物后才成立。

## 真实独立实例验收（2026-09-20）

- 用户指定新 Skill 独立实例、A1（北美草原狼）及测试买家，并明确授权代发。通过买家发送的“Skill 验收起点”锁定真实客户和会话；平台显示昵称为 atonwan，未仅凭最初提供的昵称猜测路由。
- 测试编号 `20260920-023451`。停止原消息桥并确认退出后，从 NPX 安装产物部署的独立环境启动，仅运行 A1，并限制测试客户。独立数据库仅引入该起点及其真实飞书映射，没有复制生产待发队列。
- 在本人登录的飞书网页引用该客户提醒，实际发送一条测试文字。独立实例持久化任务为 `text / SERVER_ACCEPTED`；用户随后明确确认买家已收到包含该测试编号的文字，文字真实收发闭环通过；收件结论来自用户反馈，而非仅依据任务状态。
- PNG 文件选择返回 `Not allowed`，Chrome 扩展未获本地文件访问权限；图片没有发送，独立任务库无图片任务。没有绕过限制或重复发送文字。PNG/JPG/JPEG 的独立实例实测均未完成。
- 已安全停止测试实例并释放锁，重新启动原三账号消息桥；进程证据为 RUNNING、心跳新鲜、A1/A2/A3 均 ONLINE、飞书在线。测试队列未复制回正式库，私有配置、日志、PNG 和备份保留于本地，不纳入发布。

## 维护验证命令

使用项目 .venv 解释器，并设置 PYTHONUTF8=1：

```powershell
.\.venv\Scripts\python.exe scripts/build_skill_bundle.py
.\.venv\Scripts\python.exe scripts/verify_skill_bundle.py --source-check
.\.venv\Scripts\python.exe -m ruff check src tests skills/xianyu-feishu-bridge/scripts scripts/build_skill_bundle.py scripts/verify_skill_bundle.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\skills-ref.exe validate skills/xianyu-feishu-bridge
```

参考：[Agent Skills 规范](https://agentskills.io/specification)、[skills CLI](https://github.com/vercel-labs/skills)、[skills-ref](https://github.com/agentskills/agentskills/tree/main/skills-ref)。

### 手动图片测试窗口

用户随后要求切换新实例以手动测试图片。切换前确认原实例已退出、两实例锁均空闲且没有 QUEUED/DISPATCHING 回复；已再次启动新 Skill 独立实例，仅运行 A1 并限制原测试买家。当前保留运行供用户测试，未设置自动结束时间；原三账号实例未同时启动。最新启停状态以私有 data/skill-live-test-session.json 和实际进程证据为准，图片结果待用户发送后核对。

本次启动结果：新实例主循环 RUNNING、飞书在线，但 A1 在初始化后返回 AUTH_REQUIRED（AuthRequiredError），未达到可发送状态。手动图片测试暂不可开始，需要核对对应比特窗口登录状态并恢复认证；没有发送或重发测试消息。

后续认证诊断已停止新实例：比特返回绑定窗口未运行、实时 Cookie 为空，无头刷新失败；新旧实例 API 和窗口绑定一致。等待核对用户所见窗口，当前不能进行图片测试。

用户确认比特窗口此前登录但当前关闭。已打开 A1 原绑定窗口，Local API 确认窗口运行，读取 31 条实时 Cookie，UID 与原绑定匹配；签名令牌新鲜度检查仍失败。当前等待用户刷新闲鱼消息页面后复核，尚未重新启动消息桥或发送图片。

### 分区 Cookie 修复验收

本次失败根因已确认：比特接口省略 partitionKey，旧非分区 Cookie 覆盖有效闲鱼分区 Cookie。已新增冲突时读取原窗口上下文并优先当前闲鱼站点分区的逻辑；未导航或关闭用户窗口。3 项新增回归先失败后通过，Cookie 模块 27 项、全量 413 项测试通过，Ruff 及 66 文件资源一致性校验通过。已通过标准 upgrade 将修复部署至独立实例，返回 UPGRADED，旧代码、环境和私有快照保留；没有回滚业务队列。本节之后生成的 bundle-manifest 为最新产物，前述初次安装哈希仅描述初次验收版本。

最新手动验收窗口：分区 Cookie 修复已通过 413 项回归并部署到新 Skill 独立实例；实际进程 RUNNING、心跳新鲜、A1 ONLINE、飞书在线。当前保留新实例供用户手动测试原指定买家图片，原三账号实例未启动，A2/A3 暂停。图片发送和买家收件仍待本轮确认。
### 手动图片验收结果

用户反馈成功。独立实例任务库确认文字、图片各一条，均为 SERVER_ACCEPTED；结合用户反馈，指定 A1 与测试买家的文字及单张图片真实闭环通过。本轮未确认图片具体编码，不扩展为 PNG/JPG/JPEG 各格式均通过。已安全停止测试实例并重新启动原三账号消息桥，恢复后已确认 RUNNING、心跳新鲜、A1/A2/A3 均 ONLINE、飞书在线。没有合并测试队列或重复发送。
