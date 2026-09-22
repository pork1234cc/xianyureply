# 闲鱼 × 飞书消息桥：本地 MVP

项目目录：`E:\2-AI\xianyureply`。
专属环境：项目 `.venv`，Python 3.12.14。

首次部署请先按 [README 使用说明](../README.md) 完成安装、飞书应用配置和账号绑定；本文补充运行细节与诊断步骤。上面的目录和 Python 版本为开发环境记录，其他电脑请使用自己的项目目录。

当前包含阶段 0 探针、多账号独立进程、持久化路由和双向文本转发，账号数量不限于三个。真实联调记录见 `bridge-development.md`；24 小时常驻与完整验收通过前，不应把它作为唯一收消息入口。

## 启动、停止和状态

启用 `bitbrowser.enabled: true` 后，启动时自动读取 `group_name`（默认“闲鱼”）的窗口账号，按闲鱼 UID 与本地去重并补建。5 个已登录且 UID 不同的窗口对应 5 个本地账号；已有 3 个时只补建缺少的 2 个。账号数量不限于 A1～A3，同 UID 重复窗口不会重复建号，同名不同 UID 会分别建号。新项目可将 `accounts` 配为 `[]`；已有配置和账号编号继续保留。

只同步账号、暂不启动消息桥：

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge sync-accounts
```

`run` 默认启动同步后的全部已绑定账号，`run --accounts A1 A5` 只启动指定账号。同步与运行共享单实例锁，已有服务运行时不能另行同步；新增窗口在下次启动时加入，不会运行中热添加。未登录窗口跳过并提示，已绑定窗口换号会拒绝自动改绑。发现记录保存到 `accounts/A*/account.json`，不会改写 `config.yaml`，也不会因窗口移出分组而删除本地数据。

```powershell
# 默认启动全部已完成绑定的账号：
.\.venv\Scripts\python.exe -m goofish_bridge run
# A1 单账号限制到指定自有测试客户，运行 5 分钟：
.\.venv\Scripts\python.exe -m goofish_bridge run --accounts A1 --test-customer-uid 测试客户UID --duration 300
.\.venv\Scripts\python.exe -m goofish_bridge status
.\.venv\Scripts\python.exe -m goofish_bridge stop
```

本人飞书私聊发送未引用的“状态”可查询当前账号状态、排队数量和异常数量。带引用的“状态”是普通客户正文。客户消息卡片只显示闲鱼账号展示名、客户昵称和正文，不显示 UID、会话 ID 或内部任务字段。可以直接引用客户卡片、新消息提醒，或引用已被消息桥接收并关联客户的上一条本人回复继续发送；不能引用回执、未关联的本人消息。文本按原样发送，每账号发送间隔至少 5 秒，`bridge.write_rpm_per_account` 示例值为 12 次/分钟，任务默认 10 分钟过期，不自动重发未知任务。`.env` 中 `GOOFISH_WRITE_RPM=1` 是独立诊断工具的默认值，消息桥以 YAML 额度为准。

首次客户消息使用交互卡片展示，按“闲鱼账号 + 客户 + 会话”保存固定映射。后续每条新消息在聊天底部发送一条引用原卡片的文本提醒，显示“闲鱼账号、客户昵称、新消息正文”；直接引用该提醒输入文字也可以回复对应客户。重复收到同一条源消息不会重复提醒，同名客户按内部映射区分。

客户发送单张静态图片时，先出现 `[图片]` 文字提醒；历史补拉取得原图后，机器人会再发送一条引用原客户卡片的图片消息。可以直接引用该图片回复。支持闲鱼 CDN 返回的 PNG、JPEG、WebP，其中 WebP 会在内存中转换。飞书应用须在权限管理中开通 `im:resource` 并发布生效版本；缺少权限时图片上传返回 `99991672`，机器人会发失败提示，原 `[图片]` 文字提醒仍可用于到闲鱼核对。暂不转发多张图片、动图或其他格式。

为保护草稿，客户新消息、回复结果及重启均不自动刷新或撤回已有卡片。新消息持续保存在本地；点击“查看更多”会主动刷新为最近 12 条，“收起”刷新为最近 4 条（主动刷新前请先处理输入中的草稿）。卡片输入回复仍可使用；正常排队和发送成功不推送回执，仅失败、结果未知、过期时发送附账号、客户昵称、原回复正文的异常提示。飞书中已发出不代表闲鱼已收到，连续提交可能排队，可发送未引用的“状态”查询。需要在飞书自建应用事件订阅中启用 `卡片回调（card.action.trigger）`，并保持机器人长连接事件接收。

点击引用区域可能进入飞书消息详情页。详情页连续回复只要直接关联客户卡片、提醒或上一条已关联的本人回复，即可复用固定路由。未携带 parent_id、仅带 root_id 或关联未知消息时仍拒绝转发，不按“最近客户”猜测目标。客户新消息提醒始终保留，不根据详情页是否打开暂停。手机端连续回复的具体关联仍需本人账号实测，不保证所有页面状态都能关联。

测试模式使用 `data/bridge-test.sqlite`，正式运行使用 `data/bridge.sqlite`。两套映射分离：测试模式产生的飞书转发不能在正式数据库中回复。所有账号的数据库写入集中在主进程。

初次启动从当前时间建立监控起点，不将历史旧消息灌入飞书。重连有界补拉最多 50 个会话、每会话 5 页，每页 20 条；有限会话发现不能保证平台全部历史可恢复，缺口只写入本地 `sync_state` 和日志，不推送到飞书。未完成的补拉检查点不会被后来新消息覆盖。

## 安装与恢复环境

在项目目录运行 PowerShell：

```powershell
uv venv --python 3.12.14 .venv
uv sync --python .venv\Scripts\python.exe --extra dev --locked
```

已有 `.venv` 时只运行第二行。`uv.lock` 固定全部依赖，其中飞书 SDK 1.6.8、websockets 15.0.1。需要本机 Node.js 与 Google Chrome；扫码浏览器使用 Chrome 的独立账号目录，不复用个人浏览器登录态。

首次从 `config.example.yaml` 复制得到 `config.yaml`，从 `.env.example` 配置本地 `.env` 中的 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`OPEN_ID`。开发环境已保留原工作区 `.env`，并复制到当前项目，未输出或修改密钥。

`accounts`、`data`、`logs`、`.env` 应仅允许当前 Windows 用户与 SYSTEM 访问。当前开发目录已设置对应 ACL；复制或迁移到新机器后重新检查 ACL。不要把 `.env`、浏览器 profile、Cookie 备份、数据库和 config.yaml 加入版本控制。

## 本地检查和登录

### 独立代理与客户端信息

**已启用比特来源时，只在比特窗口里管理代理和 UA，不需要填写代理环境变量。** 程序按配置分组和已绑定窗口 ID 匹配窗口，未绑定窗口 ID 的旧账号才按名称定位，校验 Cookie UID，再读取同窗口的代理及 UA。支持自定义 HTTP、HTTPS、SOCKS5（含认证），只有窗口明确设置“不使用代理”才直连。窗口设置变更在重启消息桥后生效，同一会话的请求、长连接和重连使用启动时固定的配置；飞书仍直连。

窗口读取失败、地址缺失、未知类型、动态 API 提取或全局代理配置均明确报错，不回退直连，不使用旧地址。SOCKS5 由代理解析目标域名。比特接口中的 UA 用于 HTTP、WebSocket 和 IM 注册声明，程序设备 ID 保持原值；不复制完整浏览器指纹。带认证的 SOCKS5 请在原比特窗口登录，独立 `login --qr` 不支持这一浏览器代理组合，程序会明确拒绝。

只有未启用比特来源时，才使用手工配置：本地 `.env` 填写 `GOOFISH_A1_PROXY_URL`，例如 `socks5://user:password@proxy.example:1080`（凭据特殊字符需 URL 编码），账号配置改为：

```yaml
network: {mode: proxy, proxy_url_env: GOOFISH_A1_PROXY_URL}
```

A2、A3 手工模式使用各自变量；HTTP、HTTPS、SOCKS5 都支持，配置不合法或不可达不会回退直连。启用比特后，账号下的手工 network/client 配置被忽略，不应再维护两份代理。

非比特来源可配置 `client: {user_agent: "完整桌面 Chrome UA"}`；支持 Windows/macOS/Linux 桌面 Chrome，未填写时使用固定 Windows 10 / Chrome 133。比特来源则自动读取窗口 UA，无需另填。

不要为更换代理或 UA 清空 `device.json`、`im_token.json`。现有程序设备 ID 按账号保持稳定，Token 获取和注册使用同一 ID；它与比特浏览器设备指纹不是同一概念。代理隔离与声明一致性不能保证平台风控结果。

可运行 `.\.venv\Scripts\python.exe scripts/probe_bitbrowser_proxy.py --account A1`，只读取该窗口配置，匿名验证闲鱼首页、API 域名的 TLS 与 WSS 握手，并与直连对照。不携带 Cookie、Token，不发送 IM 注册或客户消息；输出不含代理凭据。它不能代替账号认证、长期稳定性或客户收件验收。

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge doctor
.\.venv\Scripts\python.exe -m goofish_bridge login --account A1 --qr
# 或显式导入自己的 Cookie 文件：
.\.venv\Scripts\python.exe -m goofish_bridge login --account A1 --cookies C:\private\cookies.json
```

扫码后必须输入屏幕显示的完整 UID 确认绑定。A1/A2/A3 不允许互换 UID，账号续登前保存旧凭据备份。当前配置已启用比特浏览器 Cookie 源：程序读取本机 Local API 的“闲鱼”分组，并按 A1/A2/A3 的账号昵称匹配窗口；优先读取已打开窗口的实时 Cookie，窗口关闭时才使用已同步快照，快照为空、关键字段缺失或 H5 Token 已过期（含剩余不足一分钟）时，临时无头打开原账号窗口，通过原浏览器上下文加载闲鱼首页，等待有效 Cookie。无论成功、加载失败或打开请求异常，均执行关闭临时窗口并检查进程状态；关闭无法确认时明确报错。已打开但尚未取得 Cookie 时会报错等待页面加载，不关闭用户窗口。窗口关闭不等于退出登录；快照令牌过期会先自动无头刷新；无头刷新仍无法恢复时，才需要在原窗口完成登录或验证。比特浏览器不可用时启动会明确报错，不会静默混用其他浏览器账号。当前 UID 检查首先依据 Cookie 与本地人工绑定，服务端身份语义仍需协议验证。

`doctor` 只报告本地准备情况；缺少 A2/A3 等准备项时退出码为 2，不代表 A1 登录失败，也不代表已经连通平台。`status` 只读正式业务库；程序异常退出后的磁盘状态可能滞后，应结合进程实际情况检查。

## 单账号监听与已有会话验证

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge probe-watch --account A1 --seconds 120
.\.venv\Scripts\python.exe -m goofish_bridge probe-route --account A1 --marker MVP-你的随机标记 --seconds 120
.\.venv\Scripts\python.exe -m goofish_bridge probe-history --account A1 --cid 已核对的会话ID
```

`probe-route` 只匹配测试客户发来的完整标记，输出会话和客户 ID，过滤当前账号自身消息。数字字段候选明确标为 `candidate_fields_unverified`，不能直接用来构建正式路由。

监听只有在同时收到 `/reg` 成功回包和 `/s/vulcan` 后才提示就绪。连接时可能补发旧消息，收到事件总数不能直接当作测试客户的新消息数。历史查询最多 5 页、每页 20 条、总时间 60 秒，出现上限或异常游标时报告未完整；本阶段不会自动补 `new_msg` 正文。

只向本人控制的测试客户进行收发验证：

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge probe-roundtrip --account A1 --cid 已核对的会话ID --customer-uid 自有测试客户UID --text "消息桥测试回复" --confirm-test-target --seconds 60
```

命令先监听，测试客户需在已确认会话发送一条消息，匹配后才发送指定原文。发出后再让测试客户发送一条，观察 `received_after_send`。每账号最多 1 次/分钟，不创建会话。

当前收发复用同一条 WebSocket，唯一接收协程按请求 ID 分配回包。早期另开发送连接的真实测试导致原监听中断，该方案已弃用。

`SERVER_ACCEPTED` 只表示闲鱼服务端接受请求；客户是否收到需在测试客户端核对。`UNKNOWN` 不自动重发。诊断发送请求在发送前写入 SQLite；异常退出后再次启动同账号探针时，残留 `DISPATCHING` 转为 `UNKNOWN`。

## 飞书绑定与手机引用验证

自建应用开启机器人能力和长连接事件 `im.message.receive_v1`，配置私聊接收及应用发送消息权限，发布到本人可用范围。

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge bind-feishu --seconds 180
```

手机向机器人发“绑定测试”。只接收 `.env` 中 OPEN_ID 对应的本人私聊，终端展示 App ID、租户、open_id、chat_id，人工输入完整 chat_id 后保存绑定。不会自动信任第一个陌生人的事件。

```powershell
# 此命令明确向已绑定的本人私聊发送一条固定测试提示：
.\.venv\Scripts\python.exe -m goofish_bridge feishu-test-message
.\.venv\Scripts\python.exe -m goofish_bridge probe-feishu --seconds 180
```

看到长连接已建立后，在手机直接引用测试提示回复“引用测试”。将事件的 `parent_id` 与上一命令返回的 `message_id` 比较，不使用 `root_id` 或最近会话兜底。探针只核对事件，不向闲鱼转发。

## 验证和样本

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m ruff check src/goofish_bridge tests/test_bridge_*.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m goofish_bridge export-samples --output data\samples-新编号.jsonl
```

样本使用独占新建，不覆盖已有文件。`data/probe.sqlite` 中协议记录保留字段结构并对值脱敏，发送诊断表保留本地目标 ID，不导出到样本。完整客户正文和认证包不写入日志。诊断库与正式方案中的 `bridge.sqlite` 分离，不将这套验证数据结构冒充完整业务数据库。

前台按 Ctrl+C 或使用 `stop` 命令停止即可。`scripts/start-bridge.ps1` 和 `scripts/stop-bridge.ps1` 提供固定项目虚拟环境入口。本机已按用户要求配置当前用户启动目录中的 `GoofishFeishuBridge.lnk`，Windows 用户登录进入桌面后在后台启动。启动脚本先启动比特客户端（若未运行），最多等待 Local API 就绪三分钟，再启动监听。比特客户端须保持登录状态。迁移电脑时执行 `scripts/install-startup.ps1 -BitBrowserPath "比特浏览器程序完整路径"`：优先注册计划任务（失败后每分钟重试、最多三次），若系统拒绝计划任务权限，则改用当前用户启动目录快捷方式；快捷方式方式不提供计划任务的失败重启机制。已有同名任务或快捷方式拒绝覆盖。电脑需要保持开机、联网、不休眠。

## 2026-09-19 目录迁移

项目内容已从原 goofish-feishu-bridge 子目录上移至当前根目录。保留 Git 历史、本地修改及运行数据；同步修正虚拟环境和已有登录自启快捷方式的路径。根目录原有 .env 内容与项目副本一致，项目副本保留为 .env.before-directory-move。

迁移验证：非虚拟环境文件无缺失，Git HEAD 保持一致；doctor 全部通过，全量测试 377 项通过（1 条飞书 SDK 弃用提示）。消息桥已通过更新后的登录启动配置恢复启动。
