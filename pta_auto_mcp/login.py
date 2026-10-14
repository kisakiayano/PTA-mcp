import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests

from .load import load_cookies, save_cookies
from .solve import TCaptcha

log = logging.getLogger("pta.login")


def default_state_path(name: str) -> Path:
    """用户级状态目录：$PTA_STATE_DIR 或 XDG state（默认 ~/.local/state/pta-auto-mcp）。

    不写在包目录里 —— 包可能装在只读的 site-packages 中。
    """
    base = os.environ.get("PTA_STATE_DIR")
    root = Path(base) if base else Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "pta-auto-mcp"
    root.mkdir(parents=True, exist_ok=True)
    return root / name

LOGIN = "https://passport.pintia.cn/api/users/sessions"
HOME = "https://pintia.cn/auth/login"
CURRENT = "https://passport.pintia.cn/api/u/current"
AID = "194593025"
ENTRY = "https://pintia.cn/auth/login"
BASE = "https://pintia.cn"

COOKIE_FILE = default_state_path("cookies.json")

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Accept": "application/json;charset=UTF-8", "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://pintia.cn", "Referer": "https://pintia.cn/",
    "sec-ch-ua": '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-site": "same-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
}


def new_session(cookie_file: Path | str | None = COOKIE_FILE, load: bool = True) -> requests.Session:
    """建一个带站点请求头的会话；`load=True` 时尝试恢复 cookies.json。"""
    s = requests.Session()
    s.headers.update(H)
    if load and cookie_file:
        try:
            load_cookies(s, Path(cookie_file))
        except Exception as e:  # 损坏的 cookies.json 不应阻断流程
            log.warning("cookies 读取失败: %s", e)
    return s


def login(session: requests.Session, body: dict) -> tuple[int, dict]:
    """发一次登录请求（无票或带票），返回 (HTTP 状态码, JSON)。"""
    r = session.post(LOGIN, json=body, timeout=25)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:300]}


def current_user(session: requests.Session) -> dict | None:
    """校验当前会话是否有效，有效则返回 user 对象。"""
    try:
        r = session.get(CURRENT, timeout=20)
        js = r.json()
    except Exception:
        return None
    return js.get("user") if r.status_code == 200 and isinstance(js, dict) else None


def auto_login(email: str | None = None, phone: str | None = None, password: str = "",
               remember: bool = True, verbose: bool = True, max_captcha: int = 2,
               pace: float = 20.0, cookie_file: Path | str | None = COOKIE_FILE,
               reuse: bool = True) -> tuple[bool, dict, requests.Session]:
    """按需过滑块的自动登录。
    返回 (是否成功, 响应/错误体, 会话)。成功后 cookies 写入 `cookie_file`。
    """
    s = new_session(cookie_file, load=reuse)
    s.get(HOME, timeout=20)  # 预热拿 PTASession / JSESSIONID

    if reuse:
        user = current_user(s)
        if user:  # 已有有效会话，直接复用
            return True, {"user": user, "reused": True}, s

    body: dict = {"password": password, "rememberMe": remember}
    if email:
        body["email"] = email
    if phone:
        body["phone"] = phone

    for attempt in range(1, max_captcha + 2):
        code, js = login(s, dict(body))  # 第 1 次：无票
        err = (js or {}).get("error", {})
        if verbose:
            log.info("[%d] HTTP %s %s", attempt, code, json.dumps(js, ensure_ascii=False)[:200])
        if not err:
            _save(s, cookie_file)
            return True, js, s
        if err.get("code") != "GATEWAY_WRONG_CAPTCHA":
            return False, js, s  # 密码错 / 未激活 / MFA 等

        cap = TCaptcha(AID, ENTRY)
        ok, r = cap.solve()
        if not ok:
            log.warning("滑块求解失败: %s", r)
            continue
        if verbose:
            log.info("滑块通过 x=%s 用时 %sms", cap.stat.get("x"), cap.stat.get("total_ms"))

        body["ticket"], body["randStr"] = r["ticket"], r["randstr"]  # 带票重试
        code, js = login(s, dict(body))
        err = (js or {}).get("error", {})
        if verbose:
            log.info("带票重试 -> HTTP %s %s", code, json.dumps(js, ensure_ascii=False)[:200])
        if not err:
            _save(s, cookie_file)
            return True, js, s
        if err.get("code") != "GATEWAY_WRONG_CAPTCHA":
            return False, js, s

        body.pop("ticket", None)
        body.pop("randStr", None)  # 票据一次性，下一轮重新解
        if attempt <= max_captcha:
            log.warning("站点拒绝了腾讯侧有效的票据，退避 %.0fs（疑似风控窗口）", pace)
            time.sleep(pace)

    return False, {"error": {"code": "CAPTCHA_RETRY_EXHAUSTED",
                             "message": "多轮均被站点拒绝；疑似出口 IP 进入风控，建议更换出口或稍后重试"}}, s


def _save(session: requests.Session, cookie_file: Path | str | None) -> None:
    if not cookie_file:
        return
    try:
        save_cookies(session, Path(cookie_file))
    except Exception as e:
        log.warning("cookies 保存失败: %s", e)


def main() -> int:
    """命令行入口：pta-login --email ... --password ..."""
    parser = argparse.ArgumentParser(prog="pta-login", description="PTA 自动登录（按需过滑块）")
    parser.add_argument("--email", default=os.environ.get("PTA_EMAIL", ""))
    parser.add_argument("--phone", default=os.environ.get("PTA_PHONE", ""))
    parser.add_argument("--password", default=os.environ.get("PTA_PASSWORD", ""))
    parser.add_argument("--no-remember", action="store_true")
    parser.add_argument("--max-captcha", type=int, default=2)
    parser.add_argument("--pace", type=float, default=20.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    user = current_user(new_session(COOKIE_FILE))  # 有效会话直接复用，无需凭据
    if user:
        print(f"已复用会话：{user.get('nickname')}")
        return 0
    if not (args.email or args.phone) or not args.password:
        parser.error("需要 --email/--phone 与 --password（或环境变量 PTA_EMAIL/PTA_PASSWORD）")

    ok, payload, session = auto_login(args.email, args.phone, args.password, not args.no_remember,
                                      max_captcha=args.max_captcha, pace=args.pace)
    if ok:
        user = payload.get("user") or current_user(session) or {}
        print(f"登录成功：{user.get('nickname')}")
        return 0
    print(f"登录失败：{json.dumps(payload, ensure_ascii=False)}")
    return 1
