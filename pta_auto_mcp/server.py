"""MCP 服务端（stdio）：把 ``pta_tools`` 的站点操作注册成 5 个工具。

分层：``login.py`` 负责会话与登录，``pta_tools.py`` 负责 PTA 接口调用，
本文件只做答案整形、题面裁剪与输出压缩，不直接发请求。
"""

import json
import logging
import re
import os
import threading
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import __version__
from . import login as P
from . import pta_tools as T
from .pta_tools import PTAError

log = logging.getLogger("pta-mcp")

WORK_DIR = Path(os.environ.get("PTA_WORK_DIR", P.default_state_path("work"))).resolve()
COMPACT = os.environ.get("PTA_COMPACT_JSON", "1") != "0"
LOGIN_INTERVAL = float(os.environ.get("PTA_MIN_LOGIN_INTERVAL", "20"))
WRITABLE = {"PROGRAMMING", "CODE_COMPLETION", "SQL_PROGRAMMING"}
ISSUES = {
    "GATEWAY_WRONG_CAPTCHA": "站点风控窗口：票据在腾讯侧有效也被拒，应停止重试，换出口 IP 或稍后再试",
    "USER_NOT_FOUND": "会话失效，请先调用 pta_login",
    "PERMISSION_DENIED": "当前账号对该资源无权限",
    "NOT_JSON": "路径不存在被 SPA 兜底返回了 HTML",
}

server = MCPServer(
    name="pta-auto-mcp",
    version=__version__,
    instructions=(
        "PTA 练习/考试助手。流程：pta_login → pta_list_exams 取 exam_id → pull_exam_set 取题 → "
        "submit_answers(dry_run=True) 预览后 dry_run=False 提交 → pta_submission_detail 查编程题判题结果。"
        "要点：客观题按题型整卷提交（分批会互相覆盖）且不返回对错，交卷后才判；"
        "编程题逐题提交、即时判题，源码写到 work/ 下再用 program_path 提交；"
        "题目未要求小数位时按原始浮点输出。"
    ),
)

_lock = threading.RLock()
_last_login_at = 0.0


def _out(data):
    """紧凑 JSON 字符串：MCP 会把 dict 缩进美化，字符串能省约 1/3 token。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) if COMPACT else data


def _fail(exc):
    if isinstance(exc, PTAError):
        return {"ok": False, "error": exc.code, "message": exc.message,
                **({"hint": ISSUES[exc.code]} if exc.code in ISSUES else {})}
    return {"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]}


def _read_work_file(name):
    """读取 work/ 内文件；越界路径直接拒绝，避免这个工具变成任意文件读取。"""
    if not name:
        return None
    path = (WORK_DIR / name).resolve() if not Path(name).is_absolute() else Path(name).resolve()
    if path != WORK_DIR and WORK_DIR not in path.parents:
        raise PTAError("PATH_OUTSIDE_WORK_DIR", f"只允许读取 {WORK_DIR} 内的文件: {name}")
    if not path.is_file():
        raise PTAError("FILE_NOT_FOUND", f"文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def _sync_session(session):
    """登录后把 pta_tools 的全局会话换成同一个 requests.Session。"""
    T.session = T.PTASession(session)


def _brief(user):
    return {k: user.get(k) for k in ("id", "nickname", "activate", "banned")}


_OPTION_START = re.compile(r"(?m)^\s*A\s*[.．、]")


def _with_options(content, choices):
    """题面里没有 A/B/C… 选项标记时补上（部分题目的选项只存在 problemConfig 里）。"""
    if not choices or _OPTION_START.search(content):
        return content
    return content + "\n" + "\n".join(f"{chr(65 + i)}. {c}" for i, c in enumerate(choices))


def _title_of(content, width=50):
    """题面首行作为短标题。"""
    for line in (content or "").splitlines():
        if line.strip():
            return line.strip("# ").strip()[:width]
    return ""


# --------------------------------------------------------------------------- 工具

@server.tool()
def pta_login(email: str = "", password: str = "", phone: str = "", remember: bool = False,
              max_captcha: int = 2, pace: float = 20.0):
    """登录并持久化会话；已有有效会话时直接复用。滑块验证码由纯协议求解器自动通过。

    Args:
        email: 邮箱（与 phone 二选一，也可用环境变量 PTA_EMAIL）。
        password: 密码（也可用环境变量 PTA_PASSWORD）。
        phone: 手机号，不含 +86。
        remember: 是否"记住我"。
        max_captcha: 验证码重试轮数，默认 2；站点风控严格时调大无益。
        pace: 轮次间退避秒数。
    """
    global _last_login_at
    with _lock:
        session = P.new_session(P.COOKIE_FILE)
        user = P.current_user(session)  # 有效会话直接复用，不占登录配额
        if user:
            _sync_session(session)
            return _out({"ok": True, "reused": True, "user": _brief(user)})

        email = email or os.environ.get("PTA_EMAIL", "")
        password = password or os.environ.get("PTA_PASSWORD", "")
        phone = phone or os.environ.get("PTA_PHONE", "")
        if not (email or phone):
            return _out({"ok": False, "error": "MISSING_CREDENTIAL",
                         "message": "需要 email 或 phone（或用环境变量 PTA_EMAIL/PTA_PHONE）"})
        waited = time.time() - _last_login_at
        if _last_login_at and waited < LOGIN_INTERVAL:
            return _out({"ok": False, "error": "LOGIN_TOO_FREQUENT",
                         "message": f"距上次登录 {waited:.0f}s，请等 {LOGIN_INTERVAL - waited:.0f}s 再试（防触发风控）"})
        _last_login_at = time.time()
        try:
            ok, payload, session = P.auto_login(email, phone, password, remember,
                                                verbose=False, max_captcha=max_captcha, pace=pace)
        except Exception as exc:
            return _out(_fail(exc))
        _sync_session(session)
        if not ok:
            error = (payload or {}).get("error", {})
            return _out({"ok": False, "error": error.get("code", "LOGIN_FAILED"),
                         "message": error.get("message", "")})
        user = payload.get("user") or {}
        log.info("登录成功：%s", user.get("nickname"))
        return _out({"ok": True, "reused": False, "user": _brief(user)})


@server.tool()
def pta_list_exams(only_active: bool = True, limit: int = 20):
    """列出练习/考试，给出提交时要用的 exam_id。

    Args:
        only_active: True 只要未结束的（默认）；False 返回全部可见题目集。
        limit: 条数上限。
    """
    try:
        return _out({"ok": True, "exams": T.get_exam_id(limit=limit, only_alive=only_active)})
    except Exception as exc:
        return _out(_fail(exc))


@server.tool()
def pull_exam_set(problem_set_id: str, problem_types: list[str] | None = None, mode: str = "index",
                  offset: int = 0, limit: int = 0, max_content_chars: int = 700):
    """按题型取题，并用 summaries 核对各题型题量（不一致会报错，不会返回残缺数据）。

    Args:
        problem_set_id: 题目集 ID（来自 pta_list_exams）。
        problem_types: 只要这些题型，缺省全部。
        mode: index 只回 id/题型/标题（概览）；digest 只回题面（首行即标题）；content 题面更长。
        offset / limit: 分页，limit=0 表示全部（digest 建议 15~30）。
        max_content_chars: 题面截断长度。
    """
    try:
        data = T.pull_exam_set(problem_set_id)
    except Exception as exc:
        return _out(_fail(exc))

    problems, totals = [], {}
    for ptype, items in data.items():
        if problem_types and ptype not in problem_types:
            continue
        totals[ptype] = len(items)
        for item in items[offset:] if limit <= 0 else items[offset:offset + limit]:
            record = {"id": item["id"], "type": ptype}
            if mode == "index":
                record["title"] = _title_of(item.get("content"))  # 概览模式只给首行
            else:
                content = _with_options(item.get("content") or "", item.get("choices"))
                record["content"] = content[:max_content_chars]
            problems.append(record)

    return _out({"ok": True, "problem_set_id": problem_set_id,
                 "counts": totals, "returned": len(problems), "problems": problems})


def _expand(source):
    """展开答案来源：{"problemType": "...", "details": [...]} 或其列表（每题型一段）。"""
    if isinstance(source, list):
        return [item for entry in source for item in _expand(entry)]
    if not isinstance(source, dict) or "problemType" not in source or "details" not in source:
        raise PTAError("BAD_ANSWERS", '答案需形如 {"problemType":"...","details":[...]}（或其列表，每个题型一段）')
    return [{"type": source["problemType"], "problem_set_problem_id": d.get("problemSetProblemId"),
             **{k: v for k, v in d.items() if k != "problemSetProblemId"}} for d in source["details"]]


def _normalize(item):
    """把一条宽松输入整形为 pta_tools 期望的结构，返回 (题型, 提交项, 编译器)。"""
    ptype = str(item.get("type") or item.get("problem_type") or "").upper()
    pid = item.get("problem_set_problem_id") or item.get("id")
    if ptype not in T.DETAIL_FIELD:
        raise PTAError("BAD_TYPE", f"不支持的题型 {ptype!r}，可选 {sorted(T.DETAIL_FIELD)}")
    if not pid:
        raise PTAError("BAD_ANSWER_ITEM", "缺少 problem_set_problem_id")
    if ptype in WRITABLE:
        program = item.get("program") or _read_work_file(item.get("program_path") or item.get("path"))
        if not program:
            raise PTAError("BAD_ANSWER_ITEM", f"{ptype} 需要 program 或 program_path（相对 {WORK_DIR}）")
        return ptype, {"problemSetProblemId": str(pid), "program": program}, (item.get("compiler") or "PYTHON3").upper()

    raw = item.get("answer", item.get("answers"))
    if ptype == "TRUE_OR_FALSE":
        answer = {"T": "TRUE", "TRUE": "TRUE", "F": "FALSE", "FALSE": "FALSE"}.get(str(raw).strip().upper())
        if answer is None:
            raise PTAError("BAD_ANSWER_ITEM", f"判断题答案需为 T/F，收到 {raw!r}")
        return ptype, {"problemSetProblemId": str(pid), "answer": answer}, None
    if ptype == "MULTIPLE_CHOICE":
        answer = str(raw).strip().upper()
        if len(answer) != 1 or not answer.isalpha():
            raise PTAError("BAD_ANSWER_ITEM", f"单选题答案应为单个字母，收到 {raw!r}")
        return ptype, {"problemSetProblemId": str(pid), "answer": answer}, None
    if ptype == "MULTIPLE_CHOICE_MORE_THAN_ONE_ANSWER":
        if isinstance(raw, str):  # 允许 "A,C" / "A C" / "AC"
            raw = [x for x in raw.replace(",", " ").replace(";", " ").split() if x]
            if len(raw) == 1 and raw[0].isalpha():
                raw = list(raw[0])  # 连写形式拆成单字母
        if not isinstance(raw, list) or not raw:
            raise PTAError("BAD_ANSWER_ITEM", f"多选题答案应为选项字母列表，收到 {raw!r}")
        return ptype, {"problemSetProblemId": str(pid),
                       "answers": [str(x).strip().upper() for x in raw]}, None

    # 填空题：必须给数组（每空一项），不按逗号切分——答案本身可能含逗号
    if not isinstance(raw, list) or not raw:
        raise PTAError("BAD_ANSWER_ITEM", f"{ptype} 需要 answers 数组（每空一项），收到 {raw!r}")
    return ptype, {"problemSetProblemId": str(pid), "answers": raw}, None


@server.tool()
def submit_answers(exam_id: str, answers: dict | list[dict] | None = None, answers_file: str = "",
                   dry_run: bool = True):
    """提交答案：客观题按题型整卷提交，编程题逐题提交并回传判题结果。

    答案形如（每个题型一段，可给列表）：
        {"problemType": "TRUE_OR_FALSE",   "details": [{"problemSetProblemId": "...", "answer": "TRUE"}]}
        {"problemType": "MULTIPLE_CHOICE", "details": [{"problemSetProblemId": "...", "answer": "B"}]}
        {"problemType": "PROGRAMMING",      "details": [{"problemSetProblemId": "...", "program_path": "programs/p1.py"}]}

    Args:
        exam_id: 考试 ID（来自 pta_list_exams）。
        answers: 上述结构（或它们的列表）；大体积答案用 answers_file 指向 work/ 下的 JSON，避免在对话里重复贴。
        answers_file: work/ 下 JSON 文件路径，内容同 answers（整卷归档时给列表，每个题型一段）。
        dry_run: True（默认）只回提交计划，确认后传 False。
    """
    try:
        source = answers if answers else json.loads(_read_work_file(answers_file) or "[]")
        items = _expand(source)
        if not items:
            raise PTAError("EMPTY_ANSWERS", "answers 不能为空")
        batches: dict[str, list] = {}
        programs: list[tuple] = []
        for item in items:
            ptype, payload, compiler = _normalize(item)
            (programs.append((ptype, payload, compiler)) if ptype in WRITABLE
             else batches.setdefault(ptype, []).append(payload))
    except Exception as exc:
        return _out(_fail(exc))

    if dry_run:
        plan = [{"type": t, "count": len(v)} for t, v in batches.items()]
        if programs:
            plan.append({"type": "PROGRAMMING 逐题", "count": len(programs)})
        return _out({"ok": True, "dry_run": True, "planned": plan, "hint": "确认后 dry_run=false 提交"})

    results = []
    try:
        for ptype, details in batches.items():
            response = T.submit_nonwritable(exam_id, {"details": details}, ptype)
            results.append({"type": ptype, "count": len(details),
                            "submission_id": (response or {}).get("submissionId")})
        for ptype, payload, compiler in programs:
            status = T.submit_writable(exam_id, {"details": [payload]}, ptype, compiler) or {}
            for _ in range(2):  # 判题异步（约 1s），最多再等 2 秒，避免只拿到 WAITING
                if status.get("submission_id") and status.get("status") in (None, "WAITING"):
                    time.sleep(1)
                    status = T.check_program_status(exam_id, status["submission_id"]) or status
                else:
                    break
            results.append({"type": ptype, "problem_set_problem_id": payload["problemSetProblemId"],
                            "submission_id": status.get("submission_id"), "status": status.get("status"),
                            "score": status.get("score"), "failing": status.get("failing")})
    except Exception as exc:
        return _out(_fail(exc))

    return _out({"ok": True, "dry_run": False, "submitted": len(items), "results": results})


@server.tool()
def pta_submission_detail(exam_id: str, submission_id: str, max_text: int = 200):
    """查看某次提交的判题结果：总状态/得分、每个用例结果、失败用例的输出摘要。

    Args:
        exam_id: 考试 ID。
        submission_id: submit_answers 返回的 submission_id。
        max_text: 失败用例输出截断长度。
    """
    try:
        return _out({"ok": True, **T.check_program_status(exam_id, submission_id, max_text)})
    except Exception as exc:
        return _out(_fail(exc))


def main():
    log.info("pta-auto-mcp 启动 (stdio)，work=%s compact=%s", WORK_DIR, COMPACT)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
