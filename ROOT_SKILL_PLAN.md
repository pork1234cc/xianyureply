# 整个仓库作为单一 Skill 的调整方案

日期：2026-09-20。状态：阶段 A、B、C 已实施，本地验证完成；用户后续确认文字、图片测试成功并授权提交推送与重启，见 [最新验收记录](docs/skill-validation.md)。

## 1. 已确认目标

- 整个 `xianyureply` 仓库就是一个完整 Skill，唯一 `SKILL.md` 放在根目录。
- 保留根目录 `src/` 为唯一人工维护的业务源码；开发者直接修改、测试这里。
- 不再保留 `skills/xianyu-feishu-bridge/` 嵌套发布目录，不再生成 `assets/runtime/src/` 副本。
- 用户安装一个 Skill、管理一个正式实例；已有项目直接复用，不因结构调整创建第二个正式实例。
- 沿用现有多账号、比特凭据、飞书绑定、文字图片收发、固定引用路由、持久化队列、去重与限流逻辑。
- 结构调整阶段仅做本地适配；用户后续已授权提交推送并重启原服务。真实测试成功由用户反馈，本次不另向客户发送探测消息。

## 2. 目标目录

```text
xianyureply/
├── SKILL.md
├── src/goofish_bridge/、src/goofish_cli/
├── scripts/
│   ├── bridge_control.py、bootstrap.ps1、bundle.py
│   ├── build_skill_bundle.py、verify_skill_bundle.py
│   └── 原有运行与诊断脚本
├── docs/
│   ├── skill-setup.md、skill-operations.md
│   ├── skill-troubleshooting.md、skill-compatibility.md
│   └── 原有架构、开发与验收文档
├── tests/
├── pyproject.toml、uv.lock
├── config.example.yaml、.env.example
├── bundle-manifest.json
├── README.md、LICENSE、NOTICE、CHANGELOG.md
└── ROOT_SKILL_PLAN.md
```

现有 `.env`、`config.yaml`、`.venv/`、`accounts/`、`data/`、`logs/` 继续留在本机，不是发布资源。

## 3. 文件调整清单

| 当前文件 | 目标/处理 |
| --- | --- |
| 内层 SKILL.md | 根目录 SKILL.md，资源链接全部改为根目录相对路径 |
| 内层 scripts 三个管理文件 | 合并到根目录 scripts，不覆盖同名冲突文件 |
| 内层 references 四份文档 | docs/skill-*.md，更新内部链接与说明 |
| 内层 assets/runtime | 不再作为源码或发布构建输入；随旧封装完整备份至仓库外 |
| scripts/build_skill_bundle.py | 生成根目录资源清单；可导出仓库外干净安装源，不再在仓库内复制源码 |
| scripts/verify_skill_bundle.py、bundle.py | 按根目录白名单校验，区分源码资源与本机私有运行文件 |
| tests/test_skill_control.py | 改根目录入口，补根 Skill 安装、路径、接管和升级回归 |
| .github/workflows/ci.yml | 调整检查路径，增加根 Skill 校验和隔离安装验证 |
| pyproject.toml | sdist 包含根 Skill 入口、清单及开发文档；依赖和业务包版本不变 |
| .gitattributes、.gitignore | 固定发布资源换行；排除实例元数据、备份和环境 |
| README、架构、开发、验收文档 | 当前使用说明统一为根 Skill，历史验收明确标记旧布局 |
| SKILL_STANDARDIZATION_PLAN.md | 保留历史内容，在开头链接本方案并标记布局已被取代 |

## 4. 管理与实例规则

1. 管理脚本从自身路径定位仓库根 `src/`，不引用内层 Skill 或仓库外运行文件。
2. 当前 Skill 根目录已有有效项目配置和 `.venv` 时，默认管理它；显式 `--instance` 优先。保留原数据和进程身份，不为接管而改写正在使用的 instance_id。
3. 原项目的状态、预检、启停可直接通过统一管理入口调用，不要求先迁移、拷贝账号或重建环境。
4. 新用户部署仍只创建一个实例。安装目录与数据目录需要分开时，保留显式 `init --instance PATH` 能力；这不是要求再运行第二套服务。
5. 独立实例只部署一份根布局程序到 runtime；自启调用 runtime/scripts/bridge_control.py，不再额外复制完整 management 程序包。旧实例管理入口兼容需保留，不能破坏已有快捷方式。
6. 已有仓库原地使用时，init 不覆盖配置、不修改正在使用的环境；upgrade 不尝试用仓库自己覆盖自己。开发者改源码、验证并重建清单后使用已有环境，依赖变更在停服维护时处理。
7. 独立实例升级保留既有准备、互斥、备份与失败恢复规则；不自动回滚业务数据库或重发 UNKNOWN。

## 5. 发布与隐私边界

- 根 Skill 的清单列出程序、静态 JS、依赖锁、模板、必要文档和脚本；程序资源必须哈希匹配。
- 校验不递归散列整个正在运行的工作目录，避免把 Cookie、数据库、日志和 `.venv` 纳入清单。
- 导出只逐项复制校验后的白名单资源，并携带清单。目标必须是仓库外新目录，已有目标拒绝覆盖。
- 本地 `npx skills add` 使用这个干净导出目录，不直接把含真实私有文件的工作目录交给安装器。
- 远端发布仍是完整根布局仓库；Git 跟踪内容须排除运行数据。安装后的运行脚本不得依赖原开发仓库。
- 依赖、模板、源码和管理文件都只维护一份，保留原 Apache-2.0 许可及上游署名。

## 6. 实施步骤与验证

### 阶段 A：备份与入口归位

- 保存现有根源码哈希和非敏感正式进程证据，记录本次原有未提交改动。
- 将旧 skills 整个目录完整移动至仓库外唯一备份路径，核对移动前后文件哈希；不删除任何旧文件。
- 从备份取回唯一 SKILL.md、管理脚本与参考资料到目标位置。
- 验证仓库可发布范围内只有一个 SKILL.md；业务 src 字节不变。

### 阶段 B：清单、导出与管理适配

- 先补测试，验证旧路径、根目录接管和私有文件导出问题，再修改对应模块。
- 每个模块完成后运行 Ruff 与受影响测试；验证中文空格路径、任意 cwd、缺文件/篡改、路径越界与配置保留。
- 状态与预检仅只读检查正式实例；启停、初始化、升级和自启验证在隔离目录或模拟进程上执行。

### 阶段 C：文档、CI 与完整验收

- 统一 README 的 Skill 路线，详细配置沿用既有文档，开发者直接改 src。
- 全量回归、Ruff、差异空白检查、资源哈希校验、根入口链接与单入口检查。
- 隔离导出后实际执行 `npx skills@1.7.0 add`，分别验证 Codex/Claude Code 安装产物；不操作全局安装目录。
- 使用官方 skills-ref 校验 Skill；本机 quick_validate 的已知 compatibility 字段限制单独记录。
- 安装产物内验证 inspect、干净实例 init/status/doctor，以及程序加载来源；不沿用原嵌套版本的安装结论。
- 完成后更新本方案执行结果与验收文档，明确已验证/未验证事项及备份位置。

## 7. 风险与回退

- 当前工作区已有大量前序修改，全部保留，不 reset、不 clean、不覆盖业务源码。
- 移动旧封装前检查源路径为本仓库 skills，目标为明确的仓库外备份目录且不存在；保存完整文件哈希清单。
- 正式服务、配置、数据库、账号、虚拟环境和 Windows 自启项不作迁移或删除。
- 若新管理入口验证失败，可从仓库外备份恢复旧封装；业务源码和正式数据不依赖此回退。
- 后续删除仓库外备份或旧测试实例属于单独清理，需明确范围并人工确认，不包含在本次自动执行中。

## 8. 依据

- 用户本轮明确要求整个仓库就是 Skill，并要求先写完整方案再修改。
- 已读 docs/architecture.md、docs/bridge-development.md、docs/skill-validation.md 及现有管理与构建实现。
- [Agent Skills 规范](https://agentskills.io/specification) 和 [skills 安装器](https://github.com/vercel-labs/skills)；实际兼容性以本轮隔离安装结果为准。

## 9. 执行记录

- 已确认使用 `E:/2-AI/xianyureply/.venv/Scripts/python.exe`，sys.executable/sys.prefix 均指向本项目环境。
- 阶段 A 已完成：旧封装完整备份至 `E:/2-AI/xianyureply-root-skill-backup-20260920-092833/skills`，71 个文件逐项哈希一致；仓库仅保留根 SKILL.md。根 src 的 51 个文件与调整前逐字节一致。
- 阶段 B 已完成：根发布清单/白名单导出、现有项目直接管理、单份 runtime 部署、旧 management 自启转发及失败回退均已实现。先建立回归，旧实现 10 项失败；修复后相关测试通过。
- 阶段 C 已完成：README、架构、使用说明、CI、sdist 资源和发布忽略规则同步。发布文本统一 LF，保证 Windows/Linux 检出后哈希稳定；源码未因换行调整改变。
- 本轮全量业务回归 424 项通过；再补两项升级与自启回归，根布局测试共 13 项通过。最终全量重跑结果以 docs/skill-validation.md 为准。Ruff、uv lock --check、差异空白、入口链接及全部发布文件敏感标识扫描通过。
- 隔离工作区：`E:/2-AI/xianyureply-skill-check-6422deba`。NPX 1.7.0 仅发现一个根 Skill，Codex 与 Claude Code 复制安装产物均通过严格完整性校验；官方 skills-ref 校验以导出/安装后规范目录名 xianyu-feishu-bridge 执行通过。开发仓库目录名可以保留 xianyureply，无需改名或嵌套目录。
- 隔离安装产物 init 成功；status=STOPPED，doctor 如实报告空配置；实例 Python 与 goofish_bridge 加载路径均属于自己的 .venv，未使用开发仓库源码。实例没有真实账号或客户消息。
- 使用备份中的 0.1.0 原管理脚本建立空旧实例，再执行本轮 upgrade，实际返回 UPGRADED；旧 management 入口和新 runtime 入口均能查询同一实例，实例 ID 保留，旧环境/资源和快照在其 backups 中。
- 本机 quick_validate.py 不认识规范允许的 compatibility 字段，仍报告该旧版限制；未删去平台约束迎合旧工具，官方 skills-ref 校验通过。
- 正式服务仅查询状态，未重启、未迁移、未改配置或业务数据；查询时 A1/A2/A3 和飞书均 ONLINE。真实收发沿用既有验收结论，本轮未发送测试消息；没有新声明跨平台、跨宿主长期运行或真实登录自启验证。
- 后续用户已授权提交推送、重启服务并核对全部账号在线；删除旧备份和测试目录仍不在本次范围内。前序未提交的同一功能改动随本次完整发布纳入，私有运行数据保持本地。
