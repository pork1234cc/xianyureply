# 闲鱼 × 飞书消息桥：本地 MVP

项目目录：`E:\2-AI\xianyureply\goofish-feishu-bridge`。
专属环境：项目 `.venv`，Python 3.12.14。

当前包含阶段 0 探针、三账号独立进程、持久化路由和双向文本转发。真实联调记录见 `bridge-development.md`；24 小时常驻与完整验收通过前，不应把它作为唯一收消息入口。

## 启动、停止和状态

```powershell
# 默认启动已完成绑定的 A1、A2、A3：
.\.venv\Scripts\python.exe -m goofish_bridge run
# A1 单账号限制到指定自有测试客户，运行 5 分钟：
.\.venv\Scripts\python.exe -m goofish_bridge run --accounts A1 --test-customer-uid 测试客户UID --duration 300
.\.venv\Scripts\python.exe -m goofish_bridge status
.\.venv\Scripts\python.exe -m goofish_bridge stop
```

本人飞书私聊发送未引用的“状态”可查询当前账号状态、排队数量和异常数量。带引用的“状态”是普通客户正文。客户消息卡片只显示闲鱼账号展示名、客户昵称和正文，不显示 UID、会话 ID 或内部任务字段；回复回执只显示正文和最终状态。只允许直接引用机器人转发的客户消息；不能引用回执或自己的回复。文本按原样发送，每账号 1 次/分钟、10 分钟过期，不自动重发未知任务。

客户消息使用交互卡片展示。每个“闲鱼账号 + 客户”对应一张卡片，默认显示最新 4 条消息；点击“查看更多”可展开到最近 12 条，再点击“收起”恢复。卡片内输入回复并点击“发送回复”即可，不需要引用消息；完整消息仍保存在本地数据库。需要在飞书自建应用事件订阅中启用 `卡片回调（card.action.trigger）`，并保持机器人长连接事件接收。

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

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge doctor
.\.venv\Scripts\python.exe -m goofish_bridge login --account A1 --qr
# 或显式导入自己的 Cookie 文件：
.\.venv\Scripts\python.exe -m goofish_bridge login --account A1 --cookies C:\private\cookies.json
```

扫码后必须输入屏幕显示的完整 UID 确认绑定。A1/A2/A3 不允许互换 UID，账号续登前保存旧凭据备份。当前配置已启用比特浏览器 Cookie 源：程序读取本机 Local API 的“闲鱼”分组，并按 A1/A2/A3 的账号昵称匹配窗口；优先读取已同步 Cookie，必要时以无头模式打开窗口读取实时 Cookie。比特浏览器不可用时启动会明确报错，不会静默混用其他浏览器账号。当前 UID 检查首先依据 Cookie 与本地人工绑定，服务端身份语义仍需协议验证。

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

前台按 Ctrl+C 或使用 `stop` 命令停止即可。`scripts/start-bridge.ps1` 和 `scripts/stop-bridge.ps1` 提供固定项目虚拟环境入口。需要登录后自启时，人工运行 `scripts/install-startup.ps1` 注册计划任务；开发过程不会自动注册。电脑需要保持开机、联网、不休眠。
