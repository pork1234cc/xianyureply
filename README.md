# 闲鱼 × 飞书回复助手

把多个闲鱼账号的客户消息集中转发到本人的飞书机器人私聊，在飞书中通过卡片或引用消息回复对应客户。

这是运行在本地电脑上的文本消息桥，当前没有接入 AI 模型、知识库或自动回复策略。电脑需要保持开机、联网且不休眠。当前仍处于 MVP 阶段，长期常驻与完整验收进度见 [开发记录](docs/bridge-development.md)，建议保留闲鱼客户端核对消息。

## 致谢原仓库

本项目基于 [fancyboi999/goofish-cli](https://github.com/fancyboi999/goofish-cli) 修改开发。感谢原作者 **fancy** 及贡献者开源闲鱼账号认证、接口签名、消息协议与基础工具，为本项目实现闲鱼与飞书双向回复提供了重要基础。

本仓库在上游基础上聚焦多账号隔离、飞书转发与人工回复，并裁剪了商品管理、搜索、MCP 服务等能力。保留上游的 Apache-2.0 许可和署名，详见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。

## 能做什么

- **多账号收消息**：每个闲鱼账号独立进程、独立凭据和设备数据；支持 A1、A2、A3…，不限于三个账号。
- **比特浏览器接入**：按指定“闲鱼”分组自动发现已登录账号，按闲鱼 UID 去重并保存窗口绑定，读取该窗口的 Cookie、代理及 UA。
- **飞书人工回复**：客户首次消息生成对话卡片，后续消息在聊天底部提醒；卡片输入和直接引用都可回复。
- **固定目标路由**：按账号、客户和会话保存映射，不按昵称或“最近聊天的客户”猜测发送对象。
- **任务与记录持久化**：保存会话记录、回复队列和发送状态，支持去重、限流、重连及有界补拉；结果未知时不自动重发。

当前支持文本收发，以及飞书向闲鱼发送单张 PNG/JPG/JPEG 图片回复（离线测试通过，用户已确认实际发送成功）。不提供语音、文件回复，也不提供商品发布、删除、搜索或 AI Agent 插件。

## 一、准备运行环境

以下步骤面向 **Windows + PowerShell**，命令均在项目根目录执行。

| 依赖 | 用途 |
| --- | --- |
| Git、uv | 获取源码、安装锁定的 Python 依赖 |
| Python 3.12.14 | 按项目已有环境版本安装；包声明最低支持 Python 3.11 |
| Node.js（可通过 `node` 调用） | 底层 JavaScript 签名 |
| Google Chrome | 使用独立扫码登录方式时需要 |
| 比特浏览器及可用的 Local API | 使用比特浏览器自动同步账号时需要 |
| 飞书企业自建应用 | 开启机器人、配置权限和事件，并发布给本人使用 |

```powershell
git clone https://github.com/pork1234cc/xianyureply.git
cd xianyureply

# 首次安装；已有 .venv 时跳过创建环境这行。
uv venv --python 3.12.14 .venv
uv sync --python .venv\Scripts\python.exe --extra dev --locked

node --version
.\.venv\Scripts\python.exe -m goofish_bridge --help
```

请从本仓库源码安装。项目为保持兼容仍使用 `goofish-cli` 分发包名，仅安装同名 PyPI 包不能保证获得本仓库的消息桥功能。

## 二、创建本地配置

首次运行复制模板，已有文件则保留：

```powershell
if (-not (Test-Path -LiteralPath config.yaml)) {
    Copy-Item -LiteralPath config.example.yaml -Destination config.yaml
}
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

用支持 UTF-8 的编辑器修改这两个文件：

| 文件 | 需要填写的内容 |
| --- | --- |
| `.env` | 飞书 App ID、App Secret、本人 OPEN_ID；手工代理模式下的代理地址 |
| `config.yaml` | 比特浏览器开关、Local API 地址、窗口分组或手动账号列表 |

程序读取 `config.yaml` 同目录下的 `.env`；同名进程环境变量优先于 `.env`。配置文件中的相对路径以配置文件所在目录为基准。

### 配置飞书机器人

1. 在飞书开放平台创建企业自建应用，开启机器人能力，取得 **App ID** 和 **App Secret**。
2. 在权限管理中开通 `im:message.p2p_msg:readonly`（读取用户发给机器人的单聊消息）和 `im:message:send_as_bot`（以应用身份发送消息）。
3. 在事件配置中选择长连接接收事件，订阅 `im.message.receive_v1`；在回调配置中启用 `card.action.trigger`，用于卡片按钮及表单回复。
4. 发布应用版本，将本人加入应用可用范围；权限或订阅修改后确认已发布生效。本项目通过 SDK 长连接接收消息，不需要部署公网回调服务器。
5. 获取本人在**这个应用下**的 `open_id`，填写到 `.env`。它不是手机号、`user_id` 或 `union_id`，获取方式参见 [飞书官方用户 ID 说明](https://open.feishu.cn/document/home/user-identity-introduction/open-id)。

```dotenv
FEISHU_APP_ID=填写应用的AppID
FEISHU_APP_SECRET=填写应用的AppSecret
OPEN_ID=填写本人在该应用下的open_id
```

事件与订阅设置可对照 [接收消息事件](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive) 和 [长连接配置说明](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)。若后台要求先建立连接再保存长连接设置，可填好 `.env` 后运行下文的 `bind-feishu`，保持命令运行，再保存设置、发布版本并完成绑定；超时后重新运行即可。

### 方式 A：通过比特浏览器自动同步账号

适合已经用比特浏览器管理闲鱼账号的情况。

1. 启动并登录比特浏览器，确认 Local API 可用。
2. 建立名为“闲鱼”的窗口分组，将需要接入的窗口加入分组，在各窗口中登录对应闲鱼账号并完成必要验证。
3. 编辑 `config.yaml` 中下面两个顶层字段，其他配置保留。**新安装且没有本地绑定时**可把模板的示例账号列表替换为 `accounts: []`；已有绑定的项目保留原列表。

```yaml
bitbrowser:
  enabled: true
  api_url: http://127.0.0.1:54345
  group_name: 闲鱼
accounts: []
```

`api_url` 以本机比特浏览器实际地址为准，`group_name` 必须与窗口分组一致。模板默认 `enabled: false`，需要主动开启。

```powershell
# 只发现并保存账号，暂不启动转发。
.\.venv\Scripts\python.exe -m goofish_bridge sync-accounts
```

同步按 Cookie 中的 UID 建立 A1、A2… 本地账号：相同 UID 不重复建号，同名但 UID 不同的窗口分别建号。发现结果保存到 `accounts/A*/account.json`，不会改写 YAML。未登录或凭据读取失败的窗口会提示并跳过；已绑定窗口换号时拒绝自动改绑。

此模式从对应窗口读取代理和 UA，忽略账号下手填的 `network` / `client`。支持自定义 HTTP、HTTPS、SOCKS5（含认证），只有窗口明确设置“不使用代理”才直连；读取失败不回退直连，动态提取、全局代理等配置不支持。窗口代理或 UA 修改后需重启消息桥。

比特客户端需要保持可用，窗口不必一直显示。程序在初始化账号时优先读取已打开窗口的实时 Cookie；关闭窗口的快照过期时会尝试临时无头打开原窗口刷新，仍无法恢复时再到原窗口登录或验证。新增窗口在下次启动或显式同步时加入，不会运行中自动新增账号进程。

### 方式 B：不使用比特浏览器，手动登录

将 `bitbrowser.enabled` 保持为 `false`，把 `accounts` 改成自己需要的账号列表，例如：

```yaml
accounts:
  - key: A1
    name: 我的闲鱼账号
    expected_uid: ""
    data_dir: ./accounts/A1
    network: {mode: direct, proxy_url_env: null}
```

`key` 使用 A 加正整数；每个账号目录固定为 `./accounts/编号`。首次不知道 UID 时可留空，登录时核对；填写已知 UID 时使用引号保存为字符串。

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge login --account A1 --qr

# 或导入自己的 Cookie 文件，路径替换为实际文件；两种方式任选其一。
.\.venv\Scripts\python.exe -m goofish_bridge login --account A1 --cookies C:\private\cookies.json
```

扫码后按终端提示输入完整 UID 确认，程序为该账号保存独立加密凭据与绑定。多个账号分别配置、分别登录，不能把已有编号换绑到另一 UID。手工代理配置及登录限制见 [详细启动说明](docs/bridge-quickstart.md)。

## 三、绑定本人飞书私聊

先填好 `.env` 中的三项飞书配置，再执行：

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge bind-feishu --seconds 180
```

在等待期间，用 `.env` 中 `OPEN_ID` 对应的本人飞书账号，私聊机器人发送“绑定测试”。终端收到事件后会显示身份及会话信息；核对无误，输入屏幕显示的**完整 chat_id** 完成绑定。绑定保存到 `data/feishu-binding.json`。

只接收指定本人的私聊，不接受群聊或陌生人绑定。这个命令不会自动发现并替你填写 `OPEN_ID`；超时未收到事件时，先检查本人 ID、应用可用范围、权限及消息事件订阅。

## 四、检查并启动

```powershell
# 本地准备检查；不代表已连通闲鱼或飞书。
.\.venv\Scripts\python.exe -m goofish_bridge doctor

# 前台运行，默认启动全部已绑定账号；比特模式会先同步账号。
.\.venv\Scripts\python.exe -m goofish_bridge run
```

前台运行时保持终端打开。首次启动以当前时间作为监控起点，不会把全部历史消息灌入飞书。先让自己控制的测试客户发一条新消息，检查飞书卡片，再回复并到测试客户的闲鱼端确认实际收到。

只启动指定账号，或进行隔离测试：

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge run --accounts A1 A2

# 将占位内容替换成自己的测试客户 UID；限制 A1 测试 5 分钟。
.\.venv\Scripts\python.exe -m goofish_bridge run --accounts A1 --test-customer-uid 测试客户UID --duration 300
```

测试模式也会向指定测试客户真实发送回复，但使用独立的 `data/bridge-test.sqlite`；正式运行使用 `data/bridge.sqlite`。测试卡片的路由不能在正式模式中继续使用，切换模式后应使用该模式收到的新卡片。

同一项目只运行一个消息桥实例；运行期间不能另行执行 `sync-accounts`。保留的 `goofish auth` / `goofish message` 仅用于单账号诊断，不要在消息桥运行时对同一账号另开发送连接。

## 五、在飞书里回复客户

1. **查看客户消息**：首次消息显示“闲鱼客户对话”卡片，包含闲鱼账号名、客户昵称和正文；后续新消息在聊天底部单独提醒。
2. **发送回复**：在对应卡片的输入框填写文字，点击“发送回复”；或者直接引用客户卡片/新消息提醒，再输入文字发送。
3. **继续回复同一客户**：可引用上一条已经被消息桥接收并关联到该客户的本人回复。不要引用回执或没有建立关联的消息，程序不会猜测目标。
4. **查看运行情况**：直接给机器人发一条不带引用的“状态”，查看账号状态、排队数量与异常数量。引用客户消息发送“状态”会作为普通正文发给客户。

普通未引用消息不会被当作客户回复。图片回复请直接引用对应客户的卡片、提醒或已关联的本人回复，再通过飞书图片入口发送单张截图。支持 PNG、JPG、JPEG，单张最大 10 MiB、2500 万像素；这是程序限制，不代表平台上限。程序按实际图片内容识别格式，不主动压缩或重编码，飞书与闲鱼平台自身可能处理图片。

飞书应用还需具备“获取消息中的资源文件”接口要求的应用权限，具体以[官方接口页面](https://open.feishu.cn/document/server-docs/im-v1/message/get-2)及应用后台为准，权限变更后发布生效。图片与文字按同一账号队列依次发送。以文件、富文本图文或合并转发方式发送的图片暂不支持；多选图片是否拆成独立图片事件，需在手机端验证，首次请一张一张发送。

手机详情页的图片事件必须带有可识别的直接引用 `parent_id`；只有 `root_id` 时当前会拒绝，不猜测目标。界面显示已发出不等于闲鱼买家收到；下载、格式校验或上传失败会显示原因，发送结果不明不自动重发。代码升级后需要重启消息桥生效，首次使用请在本人控制的测试会话确认图片可打开、截图文字清晰且未重复。

为保护正在输入的草稿，新消息和发送结果不会自动刷新旧卡片。点击“查看更多”显示最近 12 条，点击“收起”显示最近 4 条；主动刷新前先处理输入框中的草稿。

默认每账号发送间隔至少 **5 秒**，`bridge.write_rpm_per_account` 示例值为 **12 次/分钟**；连续回复会排队。任务默认 **10 分钟过期**（`reply_ttl_seconds: 600`）。消息桥使用 YAML 中的发送额度，`.env` 的 `GOOFISH_WRITE_RPM=1` 是独立诊断工具的默认限额。

正常排队及服务端接受不单独推送回执；失败、结果未知、过期会发送异常提示。飞书中发出消息不等于闲鱼客户收到，`SERVER_ACCEPTED` 也只代表服务端接受请求。`UNKNOWN` 不自动重发，先到闲鱼核对，避免重复回复。

## 六、状态、停止与登录自启

在另一个 PowerShell 窗口进入项目目录后执行：

```powershell
.\.venv\Scripts\python.exe -m goofish_bridge status
.\.venv\Scripts\python.exe -m goofish_bridge stop
```

也可在前台按 `Ctrl+C` 停止。`stop` 提交安全退出请求，等待运行窗口退出后再重启。`status` 读取正式数据库，异常退出后记录可能滞后，也不展示测试数据库状态。

项目提供使用固定 `.venv` 的启动、停止脚本：

```powershell
.\scripts\start-bridge.ps1
.\scripts\stop-bridge.ps1

# 可选：配置 Windows 当前用户登录桌面后启动，替换成实际程序路径。
.\scripts\install-startup.ps1 -BitBrowserPath "D:\bitbrowser\比特浏览器.exe"
```

不使用比特浏览器时，可不传 `-BitBrowserPath`。安装脚本优先创建计划任务；权限不足时回退到当前用户启动目录快捷方式，已有同名入口不会覆盖。传入比特程序路径后，启动脚本会先确保客户端启动，并最多等待 Local API 就绪三分钟。计划任务支持失败重试，快捷方式不提供失败重启机制。这是**用户登录后启动**，不代表电脑尚未登录时服务就已运行。

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| `doctor` 返回退出码 2 | 查看未通过项，检查 Node.js、飞书环境变量和对应账号绑定；它只检查本地准备情况 |
| 比特账号未同步或提示 Local API 不可用 | 检查客户端登录状态、API 地址、分组名及窗口内闲鱼登录；确认 `enabled: true`，在服务停止后重新同步 |
| 比特窗口已登录，程序仍提示认证问题 | 先核对绑定窗口、实时 Cookie 与快照；过期快照不等于浏览器掉线，自动刷新失败时再到原窗口处理验证 |
| 飞书绑定一直等不到消息 | 确认 `OPEN_ID` 来自同一应用、本人可使用该应用、权限已发布，且启用了 `im.message.receive_v1` |
| 能收到卡片，但卡片按钮或发送回复无反应 | 检查 `card.action.trigger` 回调已启用并发布，消息桥长连接仍在运行 |
| 引用回复被拒绝或没有目标 | 直接引用客户卡片、新消息提醒或已关联的本人回复；不要引用回执或另一运行模式的卡片 |
| 回复排队、失败或结果未知 | 发未引用的“状态”，查看 `logs/bridge.log` 和闲鱼实际消息；不要反复提交同一条未知结果回复 |
| 修改 `.env` 后仍使用旧值 | 重新启动进程，并检查是否存在优先级更高的同名环境变量 |

重连补拉有会话数、页数和时长上限，不能保证补齐所有离线消息，必要时到闲鱼客户端核对。更详细的账号网络、协议探针与飞书引用诊断见 [启动与排障说明](docs/bridge-quickstart.md)。

## 本地数据与文档

| 路径 | 内容 |
| --- | --- |
| `accounts/A*/` | 每账号独立的凭据、UID 绑定、设备数据及自动发现记录 |
| `data/bridge.sqlite` | 正式会话、路由、回复任务和发送状态 |
| `data/bridge-test.sqlite` | 指定测试客户模式的数据 |
| `data/feishu-binding.json` | 本人飞书私聊绑定 |
| `logs/bridge.log` | 运行与排障日志 |

`.env`、`config.yaml`、`accounts/`、`data/`、`logs/` 及浏览器数据包含密钥或私密信息，不要提交到仓库或公开分享。正式数据库、WAL 和账号设备文件不是缓存，不要为解决登录问题随意清空。迁移、备份请先停止消息桥并妥善保护本地数据。

- [启动与排障说明](docs/bridge-quickstart.md)
- [架构与未来 AI 接入边界](docs/architecture.md)
- [开发及真实验收记录](docs/bridge-development.md)
- [原始方案与后续修订](docs/goofish_feishu_bridge_plan.md)（包含历史设计，不代表当前全部实现）
- [变更记录](CHANGELOG.md)（保留上游历史；旧版商品、MCP 等功能不代表当前功能）

## 开发验证

依赖由 `pyproject.toml` 和 `uv.lock` 管理。安装时包含 `--extra dev` 后，在项目目录执行：

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q
```

自动测试通过不等于真实平台联调通过，长期在线、移动端引用与客户实际收件仍需实际核对。
