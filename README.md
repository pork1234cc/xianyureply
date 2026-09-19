# 闲鱼 × 飞书回复助手

本项目基于 goofish-cli 的协议能力，提供三个独立闲鱼账号与本人飞书私聊之间的双向文本回复。

## 使用

使用项目虚拟环境执行 `python -m goofish_bridge run`。配置、安装、账号绑定、启停及诊断步骤见 [启动说明](docs/bridge-quickstart.md)。

- [架构与未来 AI 接入边界](docs/architecture.md)
- [开发及真实验收记录](docs/bridge-development.md)
- [原始方案与后续修订](docs/goofish_feishu_bridge_plan.md)

保留 `goofish auth` 与 `goofish message` 作为单账号诊断工具。多账号运行使用消息桥入口，由消息桥统一隔离账号与调度发送，不能同时使用诊断命令向正式会话另开发送连接。

## 功能范围

保留账号认证、比特浏览器凭据刷新、实时接收、有界补拉、会话记录、固定路由、去重、持久化任务、限流、风控、发送状态以及飞书卡片和引用回复。

不再提供商品发布与删除、商品搜索、分类、地址、图片上传、MCP 服务或 AI Agent 插件。以后可在消息桥上增加 AI 起草和人工确认功能；当前未接入模型、知识库或自动回复策略。

## 开发

依赖由 `pyproject.toml` 和 `uv.lock` 管理，现有分发包名 `goofish-cli` 保持兼容。Node.js 仍用于底层 JS 签名；Playwright 仍用于登录和比特窗口无头刷新。

在项目环境中执行 `python -m pytest -q`。运行状态和客户真实收件不由自动测试结果代替，尚未完成的真实验收见开发记录。

本项目保留上游 Apache-2.0 许可和署名，见 `LICENSE` 与 `NOTICE`。`CHANGELOG.md` 为上游历史记录，其中旧功能描述不代表当前功能。