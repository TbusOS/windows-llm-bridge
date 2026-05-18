# windows-llm-bridge

> 让 LLM Agent 像调函数一样驱动一台 Windows 主机：跑 `cmd.exe` / PowerShell、
> 推拉文件、调用厂商专用的 Windows 工具，统一返回 `{ok, data, error, artifacts}`。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: M0 bootstrap](https://img.shields.io/badge/status-M0%20bootstrap-orange.svg)](PLAN.md)

[English](README.en.md) · 中文

---

## 这是干什么的

**windows-llm-bridge**（简称 **wlb**）是 [`android-llm-bridge`](https://github.com/TbusOS/android-llm-bridge) 的姊妹项目。

alb 把"在一台真实安卓设备上调试"这件事变成 LLM Agent 能直接调用的工具集。
**wlb 把"在一台 Windows 主机上跑命令 / 推拉文件 / 驱动厂商工具"这件事变成同样的工具集。**

典型工作流：

1. 在 Linux 上交叉编译固件，输出到 Samba/SMB 共享目录
2. LLM Agent 通过 wlb 调用 Windows 端的厂商烧录工具
3. Agent 读回结构化进度 + 最终状态，自己决定下一步重试 / 改参数 / 报错给人

这个工作流以前是：人来回切窗口；现在是：Agent 一气呵成。

---

## 为什么需要它

很多嵌入式 / 驱动 / 固件场景下：

- **重活在 Linux**：编译、CI、测试农场，LLM Agent 用着很顺手
- **关键工具在 Windows**：厂商烧录器、JTAG GUI、产测夹具、签名打包器，**只有 Windows 二进制**

夹在中间最难受的不是切窗口，而是 **Agent 看不到 Windows 那边的状态**——一旦工具跑在另一台机器上，
Agent 的反馈闭环就断了。wlb 用结构化的工具桥把 Windows 接回 Agent 的视野。

直接对比：

| 维度         | 裸 SSH / RDP                              | wlb                                                     |
|--------------|-------------------------------------------|---------------------------------------------------------|
| 输出格式     | 自由文本                                  | `{ok, data, error, artifacts, timing_ms}` 结构化         |
| 错误信号     | 看 exit code 自己猜                       | `error.code` + `error.suggestion` 可直接喂回 Agent       |
| 危险动作     | 谁跑谁负责（`format c:` 一路畅通）        | 默认黑名单拒绝（`format` / `Format-Volume` / `bcdedit` / `Remove-Item -Recurse -Force C:\`） |
| MCP 集成     | 写胶水脚本                                | 一行 JSON 注册到 Claude Code / Cursor / Codex            |
| 工具调用     | 拼字符串                                  | 声明式 TOML 配置 + 进度正则 + 成功/失败正则（M2）        |
| 文件传输     | `scp` / 网盘                              | SFTP 或 SMB 路径自动翻译（M2）                           |

---

## 当前能力矩阵

> 本仓库现在处于 **M0 bootstrap**：骨架可装、可运行、能跑 smoke test，
> 真正的 SSH 通路在 M1。详见 [`PLAN.md`](PLAN.md)。

### 传输

| 名称   | 实现位置                       | 状态     | 用途                                              |
|--------|--------------------------------|----------|---------------------------------------------------|
| ssh    | `wlb.transport.ssh`            | planned  | M1 主通路：Windows OpenSSH Server，key-auth      |
| local  | `wlb.transport.local`          | beta     | 本地测试用 loopback，单元测试基础                 |
| http   | `wlb.transport.http`           | planned  | M2 备用通路：Windows 端跑微 agent，控制端 HTTP    |
| hybrid | `wlb.transport.hybrid`         | planned  | M2 智能路由：file → SFTP，cmd → SSH，离线 → HTTP  |

### 能力

| 名称       | CLI                       | MCP tool                       | 状态     | 说明                                             |
|------------|---------------------------|--------------------------------|----------|--------------------------------------------------|
| status     | `wlb status` / `describe` | `wlb_status` / `wlb_describe`  | beta     | 主机自检、环境信息、传输健康检查                 |
| cmd        | `wlb cmd <args>`          | `wlb_cmd`                      | beta     | `cmd.exe /c` 执行                                |
| powershell | `wlb powershell <args>`   | `wlb_powershell`               | beta     | PowerShell 5 / 7+ 自动探测，结构化返回           |
| filesync   | `wlb fs push|pull`        | `wlb_push` / `wlb_pull`        | planned  | M2：SFTP / SMB 路径翻译                          |
| tool       | `wlb tool run <name>`     | `wlb_tool_run`                 | planned  | M2：用户 TOML 声明工具，进度/成功/失败正则       |

---

## 快速开始

```bash
# 1. 安装（用户态，零 root，零系统 Python 污染）
git clone https://github.com/TbusOS/windows-llm-bridge.git
cd windows-llm-bridge
./scripts/install.sh

# 2. 在 Windows 端启用 OpenSSH Server（详见 docs/windows-side-setup.md）
#    把 scripts/windows-setup/enable-openssh.ps1 拷过去，以管理员跑一次

# 3. 配置 SSH 目标
cp .env.example .env
$EDITOR .env       # 填 WLB_SSH_HOST / WLB_SSH_USER / WLB_SSH_KEY

# 4. 自检
uv run wlb describe
uv run wlb status

# 5. 跑命令（M1 通路启用后）
uv run wlb cmd "ver"
uv run wlb powershell "Get-ComputerInfo | Select-Object OsName, OsVersion"
```

把 wlb 接到 Claude Code（或 Cursor / Codex）作为 MCP server：

```json
{
  "mcpServers": {
    "wlb": {
      "command": "uv",
      "args": ["run", "--project", "/abs/path/to/windows-llm-bridge", "wlb-mcp"]
    }
  }
}
```

完整步骤见 [`docs/quickstart.md`](docs/quickstart.md) 和
[`docs/mcp-integration.md`](docs/mcp-integration.md)。

---

## 项目结构

```
windows-llm-bridge/
├── CLAUDE.md                  # AI agent 规则（敏感词、风格、流程）
├── REQUIREMENTS.md            # 需求文档：做什么、为谁、反目标
├── PLAN.md                    # 计划文档：M0/M1/M2/M3 拆到文件级
├── README.md / README.en.md   # 介绍
├── pyproject.toml             # PEP 621 manifest（hatchling + uv）
├── src/wlb/
│   ├── infra/                 # Result/Errors/Permissions/Registry/Workspace
│   ├── transport/             # base ABC + ssh / local / http / hybrid
│   ├── capabilities/          # cmd / powershell / status / filesync / tool
│   ├── mcp/                   # FastMCP server + per-capability tool 注册
│   └── cli/                   # typer 入口 + 5 个子命令
├── scripts/
│   ├── install.sh / uninstall.sh
│   ├── check_sensitive_words.sh
│   └── windows-setup/enable-openssh.ps1
├── tests/                     # pytest，asyncio_mode=auto
├── docs/                      # architecture / quickstart / setup / mcp
└── workspace/                 # 运行时产物（不入仓）
```

---

## 设计哲学

- **结构化优先**：所有返回 `{ok, data, error, artifacts, timing_ms}`，
  错误必有 `code` + `suggestion`
- **权限默认拒**：危险命令模式黑名单（`format` / `Format-Volume` /
  `bcdedit /delete` / `Remove-Item -Recurse -Force C:\`）默认拒绝，
  必须显式 `--allow-dangerous`
- **零系统污染**：`install.sh` 不走 sudo、不动系统 Python、不写 `/etc`
- **MCP 一等公民**：每个能力同时有 CLI 子命令和 MCP tool，行为一致
- **品牌中立**：仓库内不出现任何具体厂商工具名 / SoC 型号 / 内部主机名

---

## 文档

| 文件                                | 内容                                     |
|-------------------------------------|------------------------------------------|
| [REQUIREMENTS.md](REQUIREMENTS.md)  | 需求 / 反目标 / 成功标准                 |
| [PLAN.md](PLAN.md)                  | 里程碑拆分（M0/M1/M2/M3）                |
| [docs/architecture.md](docs/architecture.md) | 分层架构 + Result 流转 + 权限模型 |
| [docs/quickstart.md](docs/quickstart.md)     | 8 步入门                          |
| [docs/windows-side-setup.md](docs/windows-side-setup.md) | Windows 端配 OpenSSH Server |
| [docs/mcp-integration.md](docs/mcp-integration.md)       | MCP 注册到 Claude Code / Cursor |
| [CLAUDE.md](CLAUDE.md)              | AI agent 工作规则                        |

---

## 贡献

PR 之前请通读 [`CLAUDE.md`](CLAUDE.md) 和 [`PLAN.md`](PLAN.md)。
特别注意：

- commit 之前 `./scripts/check_sensitive_words.sh` 必须 0 命中
- 新能力同时要补：capability module + MCP tool + CLI 子命令 + tests + 注册表条目 + README 矩阵
- 不接受 `Co-Authored-By: Claude ...` 之类的 AI 共作署名

---

## 许可

MIT — 见 [`LICENSE`](LICENSE)。
