# 回复助手架构

## 运行链路

`goofish_bridge.__main__` → `supervisor` → 每账号独立 `account_worker`。

闲鱼入站由 `goofish_adapter` 与 `messages` 解析，经主进程落库后，通过 `feishu_adapter` 转发。本人飞书卡片或引用回复经 `router` 精确定位账号、客户、会话，由 `store` 持久化并排队，再由对应账号进程发送。

## 保留模块

- `account`、`config`、`network`、`bitbrowser_cookie`：账号隔离、身份校验、直连、凭据获取和刷新。
- `goofish_cli/core` 与 `static/goofish_js_version_2.js`：会话、签名、Token、协议、限流、风控和异常。
- `messages`、`router`、`store`：消息归一化、固定路由、会话记录、去重、任务生命周期和恢复。
- `account_worker`、`supervisor`、`feishu_adapter`：同连接收发、进程管理、飞书事件和投递。
- `probe_store`、探针命令、测试与启动脚本：协议排障、回归验证和运行支持。
- `goofish_cli/commands/auth`、`commands/message`：独立诊断入口，使用 registry 自动发现。

账号、客户及会话由确定性代码和持久化映射选择。发送结果未知时不自动重发。正式业务数据库及其 WAL 不属于可清理缓存。

## 后续 AI 接入边界（尚未实现）

可在入站消息持久化后生成建议正文，交由本人确认。模型只生成正文，不选择或改写账号、客户和会话目标；确认后的发送继续经过身份校验、固定路由、持久化队列和限流。

会话记录可作为上下文来源，但后续仍需设计商品知识、模型配置、上下文范围、人工接管、草稿状态及发送前的新消息检查。当前没有实现这些 AI 能力，不引入占位模块或额外模型依赖。

## 裁剪范围

移除商品管理、搜索、分类、地址、图片上传、MCP、插件、技能安装、宣传素材及上游发布工作流。保留认证、消息诊断和全部消息桥基础设施。不改动现有回复业务逻辑、运行数据及账号绑定。