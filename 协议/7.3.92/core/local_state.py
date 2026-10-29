# -*- coding: utf-8 -*-
"""Persist the installation's device identity and successful login session."""
import json
import os
from pathlib import Path
import tempfile
import time
import uuid
from contextlib import contextmanager


STATE_DIR = Path(__file__).resolve().parents[1] / ".local"


@contextmanager
def _session_lock():
    """Serialize session replacement and conditional removal across processes."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'session.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def get_device_id() -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "device_id.txt"
    try:
        with path.open("x", encoding="utf-8") as stream:
            device_id = uuid.uuid4().hex
            stream.write(device_id)
            return device_id
    except FileExistsError:
        device_id = path.read_text(encoding="utf-8").strip()
        if len(device_id) != 32:
            raise ValueError("本地设备标识无效，请保留文件以便排查")
        return uuid.UUID(hex=device_id).hex


def load_session():
    path = STATE_DIR / "session.json"
    if not path.exists():
        return None
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(session, dict):
            raise ValueError("session must be an object")
        if not isinstance(session.get("token"), str) or not session["token"]:
            raise ValueError("missing token")
        if not session.get("uid") or str(session["uid"]) == "-1":
            raise ValueError("missing uid")
        return session
    except (ValueError, TypeError) as exc:
        raise ValueError("本地会话文件无效，请保留文件以便排查") from exc


def save_session(uid, token, unid):
    with _session_lock():
        _save_session(uid, token, unid)


def _save_session(uid, token, unid):
    if not token or uid is None or str(uid) == "-1":
        raise ValueError("不能保存缺少 Token 或 UID 的会话")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    session = {"uid": str(uid), "token": str(token),
               "unid": str(unid) if unid is not None else None,
               "saved_at": int(time.time())}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=STATE_DIR,
                                         prefix="session-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(session, stream, ensure_ascii=False)
        os.replace(temporary, STATE_DIR / "session.json")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def clear_session(uid, token):
    """Remove only the cache belonging to the confirmed invalidated session."""
    with _session_lock():
        session = load_session()
        if session is not None and str(session['uid']) == str(uid) and session['token'] == token:
            (STATE_DIR / 'session.json').unlink(missing_ok=True)
