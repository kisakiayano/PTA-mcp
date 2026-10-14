# 分发与安装

## 一、本地：把它变成一个可拉取的仓库

```bash
cd /home/ayano/pi_work/mcp_PTA

# 1) 确认不会把私密/本地产物带进仓库（.gitignore 已覆盖）
git init -b main
git status --short           # 应看不到 .venv/ cookies.json work/ *.egg-info

# 2) 首次提交
git add -A && git commit -m "feat: pta-auto-mcp 0.3.0"

# 3) 推到远端（示例）
git remote add origin git@github.com:<你的账号>/pta-auto-mcp.git
git push -u origin main
```

可选：发布到 PyPI 后，使用者就能直接用包名安装（否则用 git 地址也可以）：

```bash
uv build                     # 产出 dist/*.whl 与 *.tar.gz
uv publish                   # 需要 PyPI token
```

## 二、使用者侧安装（三种方式，任选）

```bash
# A. 不安装，直接用 uvx 从 git 拉起（最适合 MCP 客户端）
uvx --from git+https://github.com/<账号>/pta-auto-mcp pta-mcp

# B. 安装成全局命令（放进 ~/.local/bin，附带独立环境）
uv tool install git+https://github.com/<账号>/pta-auto-mcp
pta-mcp

# C. 克隆后本地开发
git clone https://github.com/<账号>/pta-auto-mcp && cd pta-auto-mcp
uv sync && uv run server.py
```

发布到 PyPI 之后，A/B 可简化为 `uvx pta-auto-mcp` / `uv tool install pta-auto-mcp`。

## 三、MCP 客户端配置

推荐用 uvx（不用预先安装，uv 自动建隔离环境并拉取依赖）：

```json
{
  "mcpServers": {
    "pta": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/<账号>/pta-auto-mcp", "pta-mcp"],
      "env": {
        "PTA_EMAIL": "you@example.com",
        "PTA_PASSWORD": "***"
      }
    }
  }
}
```

或安装后用绝对路径（启动更快，无网络依赖）：

```json
{
  "mcpServers": {
    "pta": {
      "command": "/home/<用户>/.local/bin/pta-mcp",
      "env": { "PTA_EMAIL": "you@example.com", "PTA_PASSWORD": "***" }
    }
  }
}
```

## 四、运行时目录与环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PTA_STATE_DIR` | `~/.local/state/pta-auto-mcp`（XDG） | 状态根目录 |
| `PTA_COOKIE_FILE` | `<state>/cookies.json` | 会话持久化位置 |
| `PTA_WORK_DIR` | `<state>/work` | 工具唯一允许读写的目录（放源码/答案 JSON） |
| `PTA_MIN_LOGIN_INTERVAL` | `20` | 两次登录最小间隔秒数（防风控） |
| `PTA_COMPACT_JSON` | `1` | 设 `0` 让工具返回结构化 dict（默认紧凑字符串更省 token） |
| `PTA_EMAIL` / `PTA_PASSWORD` / `PTA_PHONE` | 空 | 凭据；也可由 `pta_login` 参数传入 |

状态目录不写在包目录里——site-packages 可能只读，且避免多用户互相覆盖会话。

## 五、仓库应包含 / 不应包含

```
应包含: pyproject.toml  uv.lock  README.md  PUBLISH.md  PLATFORM_NOTES.md  LICENSE
        pta_auto_mcp/{__init__,load,solve,login,server}.py
        server.py pta_login.py（本地开发用的薄壳，可选）
不应包含: .venv/  cookies.json  work/  *.egg-info/  __pycache__/  dist/
```
