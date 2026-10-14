# pta-auto-mcp

把 PTA (pintia.cn) 的练习/考试自动化能力包成 MCP 服务（stdio），供 agent 调用。
设计目标是**把解题以外的 token 消耗压到最低**：默认只回摘要、支持从工作目录读源码/答案、判题结果不必跳出 MCP 去写脚本。

## 使用者快速开始

```bash
git clone https://github.com/kisakiayano/PTA-mcp && cd PTA-mcp
uv run server.py          # uv 自动创建 .venv 并按 uv.lock 装好依赖后启动
```

`uv run` 会自动做三件事：挑一个满足 `requires-python` 的解释器（不足则自动下载）、创建 `.venv`、
按 `uv.lock` 安装依赖。其他等价写法：

```bash
uv sync                   # 只建环境装依赖
uv run pta-login --help   # 或用安装出的命令
uv sync --no-dev          # 不要 lint 依赖（ruff），环境更小
uv sync --frozen          # 严格按 uv.lock（CI/可复现）
uvx --from . pta-mcp      # 连仓库目录都不留，直接跑
```

## 工具（5 个）

| 工具 | 用途 | 省 token 的关键点 |
|---|---|---|
| `pta_login` | 登录并持久化会话；已有有效会话直接复用 | 复用判定走 `/api/u/current`，避免重复登录触发风控 |
| `pta_list_exams` | 列出练习/考试，给出 `problem_set_id` 与 `exam_id` | 服务端按 `endAtAfter` 过滤，不做二次时间判断 |
| `pull_exam_set` | 按题型取题（`summaries` 核对题量） | `mode=index/digest/content`、`offset/limit` 分页，默认 index |
| `submit_answers` | 提交答案 | `program_path` / `answers_file` 从 `work/` 读，返回体不回显 payload |
| `pta_submission_detail` | 编程题判题结果与失败用例 | 用 `submission_id` 直接查，替代手写脚本 |

所有工具默认返回**紧凑 JSON 字符串**（`PTA_COMPACT_JSON=0` 可改回结构化 dict，省约 35% 空白开销）。

## 高效工作流（agent 视角）

```
pta_login()
pta_list_exams(only_active=True)                      → problem_set_id + exam_id
pull_exam_set(problem_set_id, mode="index")           → 题量与题面首行
pull_exam_set(problem_set_id, problem_types=["PROGRAMMING"],
              mode="digest", limit=20)                → 按题型取题面
# 编程题：源码写到 work/programs/pN.py，本地用题目样例自测
submit_answers(exam_id, answers={"problemType": "PROGRAMMING",
                                  "details": [{"problemSetProblemId": "...", "program_path": "programs/p1.py"}]},
               dry_run=True)
submit_answers(..., dry_run=False)                    → submission_id（编程题同时回判题结果）
pta_submission_detail(exam_id, submission_id)         → 复查判题 / 看失败用例
```

答案文件（`work/answers.json`）可以直接用 PTA 报文形状，按题型分段；工具会自动按题型整卷提交：

```json
[
  {"problemType": "TRUE_OR_FALSE",
   "details": [{"problemSetProblemId": "2104474233634361344", "answer": "TRUE"}]},
  {"problemType": "MULTIPLE_CHOICE",
   "details": [{"problemSetProblemId": "2104474233634361382", "answer": "B"},
               {"problemSetProblemId": "2104474233634361383", "answer": "C"}]}
]
```

字段规则：判断题 `answer` 用 `TRUE`/`FALSE`（也接受 `T`/`F`）；单选 `answer` 是单个字母；多选与填空题用
`answers` 数组（填空题每空一项，答案里可以含逗号）；编程题用 `program` 或 `program_path`。

按题型分段的写法比"每题都带 `type`"省约 30%，再配合 `answers_file`：整卷提交的调用参数从约 1.6k tokens
降到约 20 tokens（答案只在写文件时出现一次）。取题同理：`mode="index"` 先看概览，只对未完成的题型
`mode="digest"` 取题面（digest 只回题面，首行即标题）。

## 代码结构与运行时目录

```
pta_auto_mcp/
  __init__.py        # 版本号
  load.py            # cookies 读写
  solve.py           # 腾讯防水墙滑块纯协议求解器（4 请求/票，无浏览器）
  login.py           # 登录 + 会话（reactive 验证码流程）
  server.py          # MCP 服务（5 工具，stdio）
server.py            # 本地开发薄壳：uv run server.py
```

运行时状态**不写在包目录**（site-packages 可能只读），默认落在用户级状态目录：

```
~/.local/state/pta-auto-mcp/
  cookies.json       # 会话
  work/              # 工具唯一允许读写的目录（越界路径直接拒绝）
    programs/pN.py   # 编程题源码，提交时用 program_path 引用
    answers.json     # 大批量客观题答案，提交时用 answers_file 引用
```

可用 `PTA_STATE_DIR` / `PTA_COOKIE_FILE` / `PTA_WORK_DIR` 覆盖。

## 平台行为（实测，踩过的坑）

1. **客观题 = 同题型整卷 upsert**：一次 POST 会覆盖该题型此前的全部答案，因此必须一次提交该题型全部答案；
   客观题不返回对错，交卷后才判。
2. **编程/主观题逐题独立**：可反复提交，每次都重新判题；编程题 1 秒内出结果。
3. **客观题要交卷才判**：`PUT /api/exams/{exam_id}/end` 之前拿不到对错；`standard-answers` 交卷前返回空。
   因此只有编程题需要按 `submission_id` 跟踪判题结果。
4. **精度**：题目未写明小数位时，答案键通常是"原始浮点字符串"，不要自行四舍五入（`温度转换` 一题即因此 WA）。
5. **风控窗口**：短时间多次失败登录或高频解票会被网关统一拒绝（票据在腾讯侧有效也照样返回
   `GATEWAY_WRONG_CAPTCHA`）。`pta_login` 内置 20s 限频；命中窗口应换出口 IP 或等待。
6. **取题不需要 exam_id**：按题型取题的接口只依赖 `problem_set_id`；`exam_id` 仅在提交
   （`/api/exams/{exam_id}/exam-submissions`）与查判题结果时使用。

## 安装与运行

```bash
# 使用者（不装包，直接从仓库拉起）
uvx --from git+https://github.com/kisakiayano/PTA-mcp pta-mcp
# 或安装成全局命令
uv tool install git+https://github.com/kisakiayano/PTA-mcp

# 开发者
uv sync
uv run server.py          # 等价于 pta-mcp
uv run pta-login --help
```

支持 Python >= 3.10，无需 cv2（`uv sync --extra cv2` 可选加速）。

## MCP 客户端配置

把下面任一段加进客户端的 `mcpServers`。`env` 里的凭据只在没有有效会话时用于首次登录，
之后复用 `~/.local/state/pta-auto-mcp/cookies.json`。

远程拉取（不安装，跟随仓库默认分支的最新提交）：

```json
{
  "mcpServers": {
    "pta": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/kisakiayano/PTA-mcp", "pta-mcp"],
      "env": { "PTA_EMAIL": "you@example.com", "PTA_PASSWORD": "***" }
    }
  }
}
```

全局安装后（启动最快，无网络依赖）：

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

本地 clone 开发（改代码立即生效）：

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

也可先 `uv sync` 再指向 `/abs/path/to/PTA-mcp/.venv/bin/pta-mcp`（启动更快）；三种写法的启动开销
差异与 Windows 路径写法见 `PUBLISH.md`。

常用环境变量：`PTA_STATE_DIR`（状态根目录，默认 `~/.local/state/pta-auto-mcp`）、`PTA_COOKIE_FILE`、
`PTA_WORK_DIR`、`PTA_MIN_LOGIN_INTERVAL`（登录限频，默认 20s）、`PTA_COMPACT_JSON`（设 0 返回结构化
dict）；完整说明见 `PUBLISH.md` 第四节。

