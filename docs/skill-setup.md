# 首次部署

运行主机需 Windows、PowerShell、Python 3.11+（开发验证为 3.12）、uv 和 Node.js。依赖安装需要联网。比特方式需已登录的比特客户端和 Local API；独立扫码需本机 Chrome。浏览器和操作系统不包含在包中。

整个仓库就是 Skill，SKILL.md、src 和 scripts 都在根目录。已有项目直接用自己的 .venv 调用 scripts/bridge_control.py status/doctor；不要复制配置、账号或重新创建实例。根项目 init 检查并沿用原环境，不改进程实例身份。

1. 通过已识别解释器执行 scripts/bridge_control.py inspect。
2. 在用户指定绝对目录执行 init --instance PATH，只部署一个实例。可指定根 Skill 目录原地运行；若安装器会替换安装目录，则建议使用 LOCALAPPDATA/XianyuFeishuBridge/instances/default 保存数据。独立目录下 runtime 是同一实例的程序文件，不是第二个服务。模板只在缺失时复制；重复 init 不覆盖配置。依赖使用根 uv.lock 和 --locked 安装到实例 .venv；失败保留资源，可补好环境后重复 init。
3. 在实例 .env 本地填写 FEISHU_APP_ID、FEISHU_APP_SECRET、OPEN_ID。不要要求把 Cookie 或整个配置贴到聊天。根项目原地运行时，这些文件也只能留在本机，禁止进入发布包。
4. 在 config.yaml 设置 bitbrowser.enabled=true、Local API 地址及窗口分组；accounts 可为空，由 sync-accounts 自动发现。同 UID 去重，不更改已有身份。手动登录则添加账号配置，见下例。
5. 飞书企业自建应用开启机器人，配置 im:message.p2p_msg:readonly、im:message:send_as_bot，图片下载另需应用的消息资源读取权限。订阅 im.message.receive_v1 与 card.action.trigger，发布给本人使用。权限由飞书后台当前可选项及实际检查确认。
6. 使用实例解释器、绝对配置路径执行以下原 CLI，按真实缺项操作。人工身份输入不由 Agent 代填。无交互终端的宿主把命令交给用户在本机运行。

```powershell
$env:VIRTUAL_ENV = "$InstanceRoot/.venv"
$env:PATH = "$InstanceRoot/.venv/Scripts;" + $env:PATH
$env:PYTHONUTF8 = '1'
& "$InstanceRoot/.venv/Scripts/python.exe" -m goofish_bridge --config "$InstanceRoot/config.yaml" sync-accounts
# 手工来源才执行 login；用户核对完整 UID。
& "$InstanceRoot/.venv/Scripts/python.exe" -m goofish_bridge --config "$InstanceRoot/config.yaml" login --account A1 --qr
& "$InstanceRoot/.venv/Scripts/python.exe" -m goofish_bridge --config "$InstanceRoot/config.yaml" bind-feishu
```

手动账号示例（比特关闭时使用；代理按各账号独立配置）：

```yaml
accounts:
  - key: A1
    name: 我的闲鱼账号
    expected_uid: ""
    data_dir: ./accounts/A1
    network: {mode: direct, proxy_url_env: null}
```

最后 doctor 检查本地缺项，start 等待就绪。文字与每种图片格式必须在指定测试会话分别验收。init 不登录、不启动、不创建飞书应用、不发送消息。
