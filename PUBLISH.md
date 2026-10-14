# 分发与安装

## 使用者侧安装（三种方式，任选）

```bash
# A. 不安装，直接用 uvx 从 git 拉起（最适合 MCP 客户端）
uvx --from git+https://github.com/kisakiayano/PTA-mcp pta-mcp

# B. 安装成全局命令（放进 ~/.local/bin，附带独立环境）
uv tool install git+https://github.com/kisakiayano/PTA-mcp
pta-mcp

# C. 克隆后本地开发
git clone https://github.com/kisakiayano/PTA-mcp && cd PTA-mcp
uv sync && uv run server.py
```

## MCP 客户端配置

推荐用 uvx（不用预先安装，uv 自动建隔离环境并拉取依赖）：

```json
{
  "mcpServers": {
    "pta": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/kisakiayano/PTA-mcp", "pta-mcp"],
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

本地部署（clone 到本机后跑，适合调试和改代码）——两种写法任选：

```json
{
  "mcpServers": {
    "pta": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/PTA-mcp", "server.py"],
      "env": { "PTA_EMAIL": "you@example.com", "PTA_PASSWORD": "***" }
    }
  }
}
```

```json
{
  "mcpServers": {
    "pta": {
      "command": "/abs/path/to/PTA-mcp/.venv/bin/pta-mcp",
      "env": { "PTA_EMAIL": "you@example.com", "PTA_PASSWORD": "***" }
    }
  }
}
```

第二种写法需要先执行上一节 C 里的 `uv sync`；两种写法的差异：

| 写法 | 启动开销 | 适合 |
|---|---|---|
| `uv run --directory ...` | 每次多约 1~2s（uv 校验 lock 与环境），必要时自动 `uv sync` | 换机器/换分支后忘同步也不会报错；依赖改了自动跟进 |
| `.venv/bin/pta-mcp` | 最快（直接启动） | 环境已 `uv sync` 且不常改依赖 |

两种都指向**同一份源码**：项目是 editable 安装，改 `pta_auto_mcp/*.py` 后立刻生效；只有改动依赖
（`pyproject.toml`）后才需要重跑 `uv sync`。Windows 下路径用正斜杠或双反斜杠，且 venv 脚本位置是
`.venv\\Scripts\\pta-mcp.exe`：

```json
{ "command": "C:/Users/me/PTA-mcp/.venv/Scripts/pta-mcp.exe" }
```

无论哪种写法，运行时状态都写在用户级目录（`~/.local/state/pta-auto-mcp/`，可用 `PTA_STATE_DIR` 改），
不会污染 clone 出来的工作区。

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
应包含: .gitignore  pyproject.toml  uv.lock  README.md  PUBLISH.md  PLATFORM_NOTES.md  LICENSE
        pta_auto_mcp/{__init__,load,solve,login,pta_tools,server}.py
        server.py（本地开发薄壳）
不应包含: .venv/  cookies.json  work/  *.egg-info/  __pycache__/  dist/
```
