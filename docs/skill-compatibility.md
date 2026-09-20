# 兼容范围与验收

Skill 布局版本 0.2.0；业务包沿用 0.4.0，运行平台是 Windows。整个仓库根目录就是一个 Skill，使用标准 frontmatter、相对资源和普通命令，不依赖专有工具名、MCP 服务或模型 API Key。

格式遵循 https://agentskills.io/specification 。安装器命令以 https://github.com/vercel-labs/skills 为准，本地验收固定 skills 1.7.0：

```powershell
npx --yes skills@1.7.0 add SOURCE --skill xianyu-feishu-bridge --agent codex --copy --yes
npx --yes skills@1.7.0 add SOURCE --skill xianyu-feishu-bridge --agent claude-code --copy --yes
```

在隔离目录运行，不加 --global。SOURCE 为通过 scripts/build_skill_bundle.py --output 导出的干净目录，或已发布的根布局远端仓库。禁止直接使用含 .env、账号、数据库、.venv 的本机工作目录作为安装源。安装只复制 Skill，不初始化实例。发布版本包含根 SKILL.md、src、scripts、docs、依赖及模板，安装后不依赖原开发仓库。

| 环境 | 支持边界 |
| --- | --- |
| Codex、Claude Code | 同一包、同一管理命令；安装验收与客户端发现/调用验收分开记录 |
| Cursor、OpenCode、Hermes 等 | 能读取标准 Skill 且能调用 Windows 命令时可使用；未经客户端测试不称为实测支持 |
| 无 Skill 发现但有文件与命令能力的 Agent | 用户指向 SKILL.md 后可手工使用 |
| 纯聊天模型、Linux/macOS/WSL/云端容器 | 能阅读说明，首版不能直接运行 Windows 桥；不提供远程执行服务 |

跨客户端自动发现、宿主退出后的存活、真实登录自启及买家文字/各图片格式收件必须分别记录。安装器列出支持某 Agent 不等于该客户端已验收。发行验收证据位于维护仓库 docs/skill-validation.md；本文件不承诺未记录的能力。
