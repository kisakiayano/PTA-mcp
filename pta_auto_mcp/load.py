import json
from pathlib import Path

import requests

COOKIE_FILE = Path("cookies.json")


def _as_path(path) -> Path:
    return path if isinstance(path, Path) else Path(path)


def save_cookies(session, path=COOKIE_FILE):
    p = _as_path(path)
    data = requests.utils.dict_from_cookiejar(session.cookies)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_cookies(session, path=COOKIE_FILE):
    p = _as_path(path)
    if not p.exists():
        return False
    data = json.loads(p.read_text(encoding="utf-8"))
    session.cookies = requests.utils.cookiejar_from_dict(data)
    return session
