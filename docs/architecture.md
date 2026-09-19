# 回复助手架构

## 运行链路

`goofish_bridge.__main__` → `supervisor` → 每账号独立 `account_worker`。

闲鱼入站由 `goofish_adapter` 与 `messages` 解析，经主进程落库后，通过 `feishu_adapter` 转发。本人飞书卡片或引用回复经 `router` 精确定位账号、客户、会话，由 `store` 持久化并排队，再由对应账号进程发送。

## 保留模块

- `account`、`config`、`network`、`bitbrowser_cookie`：账号隔离、身份校验、独立出口、凭据获取和刷新。
- `goofish_cli/core` 与 `static/goofish_js_version_2.js`：会话、签名、Token、协议、限流、风控和异常。
- `messages`、`router`、`store`：消息归一化、固定路由、会话记录、去重、任务生命周期和恢复。
- `account_worker`、`supervisor`、`feishu_adapter`：同连接收发、进程管理、飞书事件和投递。
- `probe_store`、探针命令、测试与启动脚本：协议排障、回归验证和运行支持。
- `goofish_cli/commands/auth`、`commands/message`：独立诊断入口，使用 registry 自动发现。

账号、客户及会话由确定性代码和持久化映射选择。发送结果未知时不自动重发。正式业务数据库及其 WAL 不属于可清理缓存。

## 账号网络与客户端声明

启用 `bitbrowser.enabled` 时，按分组和账号名称唯一定位窗口，校验其 Cookie UID 与本地绑定相符，从同一窗口 `/browser/detail` 读取代理及 `browserFingerPrint.userAgent`。窗口配置是唯一来源，忽略手工 `network` / `client`，不需要维护代理环境变量。只有明确 `proxyType=noproxy` 才直连；支持自定义 HTTP、HTTPS、SOCKS5 和认证，动态提取、全局代理、SSH、缺失字段或读取失败均拒绝，不用旧地址猜测。Cookie 和运行配置在 load_session 时一次获取，避免入口与 Worker 构造重复刷新。

未启用比特来源时，保留 `network.mode=direct/proxy` 和 `proxy_url_env` 手工方式。HTTP Session 和 WebSocket 连接工厂共用同一个 NetworkProfile，监听、收发、历史查询及重连不另选出口；窗口修改在重启账号进程后生效。SOCKS5 统一为 socks5h，由代理解析目标域名；代理失败不回退直连。飞书继续直连。PySocks 和 python-socks 分别服务于 HTTP 与 WebSocket；websockets 限定 15.x，锁定 15.0.1，特殊字符认证通过隔离连接子类解码，不修改第三方模块全局函数，保持 HTTPS 代理及目标 WSS 的 TLS 验证。

HTTP 请求头、WebSocket 握手和 IM 注册共用一个 ClientProfile，Client Hints 和 IM 附加版本信息由 UA 派生。比特来源使用窗口 UA，其他来源可显式配置 Windows/macOS/Linux 桌面 Chrome UA，未配置时默认 Windows 10 / Chrome 133。独立扫码保留真实 Chrome 声明，仅应用账号网络；带认证的 SOCKS5 应在原比特窗口登录，独立扫码入口明确拒绝。程序只读比特代理和指纹字段，不改写窗口配置，不自动轮换代理或指纹。

`device.json` 是程序自身的稳定 IM 设备 ID，按账号路径显式读取，并同时用于 Token 获取和 IM 注册。已有 ID 不因代理、UA 或 Cookie 更新而重建；不导入、覆盖为比特设备指纹。Cookie 导入仅携带 Cookie 记录，不包含完整浏览器指纹、localStorage 或浏览器网络栈。统一客户端声明不等于复制浏览器设备环境，也不保证平台风控结果。

## 后续 AI 接入边界（尚未实现）

可在入站消息持久化后生成建议正文，交由本人确认。模型只生成正文，不选择或改写账号、客户和会话目标；确认后的发送继续经过身份校验、固定路由、持久化队列和限流。

会话记录可作为上下文来源，但后续仍需设计商品知识、模型配置、上下文范围、人工接管、草稿状态及发送前的新消息检查。当前没有实现这些 AI 能力，不引入占位模块或额外模型依赖。

## 裁剪范围

移除商品管理、搜索、分类、地址、图片上传、MCP、插件、技能安装、宣传素材及上游发布工作流。保留认证、消息诊断和全部消息桥基础设施。不改动现有回复业务逻辑、运行数据及账号绑定。
