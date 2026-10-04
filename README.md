# Hermes Light — macOS ARM64 个人构建

只构建连接远端 Hermes 的 Light 桌面客户端，适用于 Apple Silicon Mac（含 M5）。
这是个人测试包，不是 Nous Research 官方发行包。

## 开始构建

1. 打开本仓库的 **Actions**。
2. 如果 GitHub 提示，允许在本仓库运行 Actions。
3. 选择 **Build Hermes Light for macOS ARM64**。
4. 点 **Run workflow**，分支选择 `main`。
5. 首次保留默认 `source_commit`（官方仓库完整、固定的 40 位 SHA）。
6. 点绿色 **Run workflow** 开始，在运行页面查看进度。

没有 push、PR 或定时触发。提交文件本身不会启动构建。
仓库的 Deploy key 仅用于推送文件，不具备 GitHub Actions API 登录能力；手动启动需使用已登录 GitHub、具有仓库写权限的浏览器账号。

## 下载与安装

工作流绿色成功后，运行详情页底部会有：

- `hermes-light-macos-arm64-<run-id>`：DMG、ZIP、`SHA256SUMS.txt`、`BUILD-INFO.json`。
- `hermes-light-build-logs-<run-id>`：构建与启动日志，失败时也尽量保留。

下载第一个 Artifact，解压外层 GitHub ZIP，然后打开其中的 DMG，将 `.app` 拖到 Applications。
安装器保留 14 天，日志保留 7 天。

个人构建只做 **ad-hoc 签名**，没有 Developer ID 身份，也未经 Apple 公证。
macOS 可能阻止首次打开；仅在确认来源是你自己这次构建、校验正确后，按 Apple 的“系统设置 → 隐私与安全性 → 仍要打开”流程处理。
不要关闭 Gatekeeper、SIP 或全局安全保护。

如需命令行校验，进入解压目录运行 `shasum -a 256 -c SHA256SUMS.txt`。

## 连接你的后端

打开 Light 后，在 **Settings → Gateways → Remote gateway** 填写 UU 隧道在客户端机器提供的地址，并按界面完成后端认证。
例如客户端本地映射为 19119 时，地址为 `http://127.0.0.1:19119`。
远端当前端口需要单独核对；动态端口在重启后可能变化。
不要公开未鉴权的后端。此仓库不配置隧道、不改远端监听地址或认证策略。

## 验证边界与安全

- 拉取官方 `NousResearch/hermes-agent` 的指定完整 commit SHA，调用官方 `scripts/bundles/desktop.py --variant light`。
- 默认 SHA 是创建这个工作流时已核对构建接口的官方源码；不是“已验证与你的后端完全匹配”。更换 SHA 后仍需接入验收。
- Actions 使用原生 `macos-15` ARM64 runner，并检查实际 `uname -m`。
- 检查包内 Light stamp、源码身份、ARM64 架构、ad-hoc 签名，以及未带 `agent-payload`。
- 在云端隔离用户目录启动客户端 15 秒，检查没有提前退出；这不是完整 UI 功能或 M5 安装验收。
- M5 实际安装、审批交互、远端登录、HTTP/WebSocket、会话展示仍需在你设备上验证。
- 不需要添加 Actions Secrets；不上传 `.hermes`、API Key、私钥、OAuth、会话、记忆或定时任务。
- Workflow token 只有 `contents: read`。第三方 Actions 固定到完整 commit SHA，拉源码后不保留 Git 凭据。
- 不发布 GitHub Releases、不接官方自动更新、不访问官方签名或云存储账号。
- 构建失败不会被标记为成功；请提供运行页面链接排查，不要发送任何凭据。

## 工作流静态测试（可选）

在独立 Python 环境安装 `PyYAML==6.0.2`，运行：

```sh
python -m unittest discover -s tests -v
```

这些测试验证手动触发策略、固定 SHA、构建参数、权限、校验与产物契约、Shell/Python 语法；不代替真实云端构建。
