# Codex Relay

项目原名 Codex Remote。旧命令 `codex-remote`、`codex-remote-manage` 继续作为兼容入口，
已有的数据目录和 systemd 服务沿用原有标识。

[English](README.md) · [部署](docs/deployment.md) · [运维与备份](docs/operations.md)

在自己的服务器上运行 Codex，用手机和浏览器继续对话、补充指令、处理审批和查看执行结果。
服务器使用已有的 Codex 身份验证和模型供应商配置；网页登录使用独立访问口令，不填写模型 API key。

当前为 **Alpha 版本，支持 Linux、Python 3.11+，面向个人多设备使用**。界面默认英语，可在登录页或顶栏切换为简体中文。
这是独立社区项目，与 OpenAI 无隶属关系。请单独安装 Codex，并遵守模型服务商的使用规则。

## 已有能力

- 手机 Web/PWA、亮暗模式、运行过程和本轮结束标记。
- 多设备会话同步、历史分页、断线重连、工作目录浏览和会话管理。
- 运行中补充指令、整轮结束后排队、停止、审批和用户问答。
- 图片与文件粘贴、拖放、文件预览、工具图片查看。
- Markdown、代码和 diff 高亮、数学公式、Mermaid 图表。
- 原生目标与权限命令（需要兼容的 Codex 版本）。
- 登录设备撤销、可选 Web Push、脱敏诊断、网关备份恢复。

Enter 发送；Ctrl / ⌘ + Enter 或 Shift + Enter 换行，兼容输入法选字。

## 安装

先单独安装并配置 Codex，再在此仓库内执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps .
.venv/bin/codex-relay --workspace "$HOME/projects/example" \
  --state "$HOME/.local/state/codex-remote"
```

工作目录必须已经存在。默认监听 `127.0.0.1:8765`，访问口令位于所指定状态目录的
`access-token` 文件中。本地查看后填写网页登录框，不要公开口令。
如果 home 目录受配额限制或位于网络文件系统，请将 `--state` 指向容量充足的本地磁盘。
不要直接将 SQLite 运行数据库放在 NAS/NFS 上。

手机访问需要可达的网络和正确的 `--origin`。完整 PWA 与 iPhone 后台通知还需要 HTTPS、
兼容系统及主屏幕安装；普通 HTTP 内网地址不提供完整通知体验。
详见[部署说明](docs/deployment.md)。

## 独立与共享模式

默认独立模式管理自己的 Codex 子进程；关闭浏览器不会停止任务，停止网关则会停止它的子进程。
独立模式不导入原终端会话。

显式设置 `--transport shared` 可以连接已有的兼容共享后台，增加 `--browse-existing`
可以发现已有会话。浏览列表不接管任务，主动选择会话才尝试连接。其他进程持有写入锁时，
网关不会强制解锁、终止进程或自动接管。共享模式关闭网关只断开自己的连接。

默认权限为 `workspace-write` 和 `on-request`。Full Access 必须主动配置；配置后主动连接
及恢复的会话会在后续轮次应用默认权限。`--workspace` 不是限制 agent 的操作系统沙箱。
不同用户应分别运行实例；多个设备登录同一实例并没有用户间权限隔离。

## 文档和维护

- [支持范围与兼容性](docs/compatibility.md)
- [HTTPS、中转与 systemd 部署](docs/deployment.md)
- [容量、清理、备份、恢复与升级](docs/operations.md)
- [安全边界](SECURITY.md)
- [贡献与测试](CONTRIBUTING.md)

项目代码采用 Apache-2.0；第三方库保留原始许可证。
