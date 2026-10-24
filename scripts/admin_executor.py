#!/usr/bin/env python3
"""Host-side whitelisted executor for the QQ admin agent.

Runs on the Docker host (root) and listens on a Unix socket mounted into the
chatbot container. It intentionally exposes no arbitrary shell: only a small
allowlist of service, file and command operations.
"""
from __future__ import annotations

import hmac
import json
import os
import shlex
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(os.getenv("ADMIN_EXECUTOR_ROOT", "/opt/qq-chatbot")).resolve()
SOCKET_PATH = Path(os.getenv("ADMIN_EXECUTOR_SOCKET", str(ROOT / "data" / "admin.sock")))
TOKEN_FILE = Path(os.getenv("ADMIN_EXECUTOR_TOKEN_FILE", str(ROOT / "data" / "admin-executor.token")))
AUDIT_LOG = ROOT / "data" / "admin-audit.log"
KILL_SWITCH = ROOT / "data" / "admin-executor.disabled"
MODE = os.getenv("ADMIN_EXECUTOR_MODE", "safe").strip().lower()
MAX_WRITE_BYTES = 2 * 1024 * 1024
MAX_READ_BYTES = 200_000
MAX_OUTPUT_CHARS = 50_000
MAX_COMMANDS_PER_MINUTE = 30
DENY_PARTS = {".env", ".git", "data", "build", "dist", "chatbot.egg-info"}
COMPOSE = ["docker", "compose", "-p", "qqbot"]
ALLOWED_COMPOSE = {"ps", "logs", "restart", "build", "up"}
_command_times: list[float] = []
_command_lock = threading.Lock()


def _token() -> str:
    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(__import__("secrets").token_urlsafe(32), encoding="utf-8")
        TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def _audit(action: str, args: dict[str, Any], ok: bool, detail: str = "") -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "args": {key: value for key, value in args.items() if key not in {"content", "token"}},
            "ok": ok,
            "detail": detail[:300],
        }
        with AUDIT_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _safe_path(relative: str, *, write: bool = False) -> Path:
    candidate = (ROOT / relative).resolve()
    if candidate != ROOT and ROOT not in candidate.parents:
        raise ValueError("路径越界")
    if any(part in DENY_PARTS for part in candidate.relative_to(ROOT).parts):
        raise ValueError("路径不允许访问")
    if write and candidate.suffix in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("不能直接写数据库文件")
    return candidate


def _full_path(value: str, *, write: bool = False) -> Path:
    """Full-mode path resolution: absolute host paths are allowed."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    candidate = path.resolve()
    if str(candidate).startswith(("/proc", "/sys", "/dev")):
        raise ValueError("路径不允许访问")
    if write and candidate.suffix in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("不能直接写数据库文件")
    return candidate


def _read_path(value: str, *, write: bool = False) -> Path:
    return _full_path(value, write=write) if MODE == "full" else _safe_path(value, write=write)


def _rate_limit() -> None:
    now = time.monotonic()
    with _command_lock:
        _command_times[:] = [stamp for stamp in _command_times if now - stamp < 60]
        if len(_command_times) >= MAX_COMMANDS_PER_MINUTE:
            raise ValueError("命令频率超限，请稍后再试")
        _command_times.append(now)


def _run_shell(command: str, timeout: float = 120) -> str:
    result = subprocess.run(
        ["bash", "-lc", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "DEBIAN_FRONTEND": "noninteractive"},
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise RuntimeError(f"命令退出码 {result.returncode}: {output[:500]}")
    return output[:MAX_OUTPUT_CHARS]


def _run(argv: list[str], timeout: float = 120) -> str:
    result = subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"命令退出码 {result.returncode}: {output.strip()[:500]}")
    return output.strip()


def _schedule(argv: list[str], delay: float = 3.0) -> None:
    def worker():
        time.sleep(max(0.5, delay))
        try:
            subprocess.run(argv, cwd=ROOT, check=False, capture_output=True, text=True, timeout=900)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def _allowed_command(command: str) -> list[str]:
    argv = shlex.split(command)
    if not argv:
        raise ValueError("空命令")
    if set(argv[0]) & set(";&|<>`$"):
        raise ValueError("命令包含不允许的字符")
    if argv[:2] == ["git", "status"] or argv[:2] == ["git", "diff"] or argv[:2] == ["git", "log"]:
        return argv
    if argv[:3] == ["python", "-m", "pytest"] or argv[:3] == ["python", "-m", "ruff"]:
        return argv
    if argv[:3] == COMPOSE:
        if len(argv) < 4 or argv[3] not in ALLOWED_COMPOSE:
            raise ValueError("docker compose 子命令不在白名单")
        if argv[3] == "restart" and (len(argv) < 5 or argv[4] != "chatbot"):
            raise ValueError("只允许重启 chatbot 服务")
        return argv
    raise ValueError("命令不在白名单")


def execute(action: str, args: dict[str, Any]) -> dict[str, Any]:
    if action == "status":
        return {"output": _run([*COMPOSE, "ps"], timeout=30)}
    if action == "logs":
        lines = max(1, min(1000, int(args.get("lines", 100))))
        return {"output": _run(["docker", "logs", "--tail", str(lines), "qqbot-chatbot"], timeout=30)}
    if action == "restart":
        _schedule([*COMPOSE, "restart", "chatbot"], float(args.get("delay_seconds", 3)))
        return {"output": "已计划重启 chatbot，约 3 秒后执行"}
    if action == "rebuild":
        _schedule(["bash", "-lc", " && ".join([
            " ".join([*COMPOSE, "build", "chatbot"]),
            " ".join([*COMPOSE, "up", "-d", "chatbot"]),
        ])], float(args.get("delay_seconds", 3)))
        return {"output": "已计划重建并重启 chatbot，构建完成后生效"}
    if action == "migrate":
        return {"output": _run([*COMPOSE, "run", "--rm", "--no-deps", "chatbot", "python", "-m", "database.migrate"], timeout=300)}
    if action == "test":
        # The runtime image does not ship dev test dependencies; this is a
        # fast syntax check. Run the full pytest suite on a development host.
        return {
            "output": _run(
                ["docker", "run", "--rm", "--entrypoint", "python", "qqbot-chatbot", "-m", "compileall", "-q", "/app"],
                timeout=180,
            )
            or "语法检查通过"
        }
    if action == "read_file":
        path = _read_path(str(args.get("path", "")))
        max_bytes = max(1, min(MAX_READ_BYTES, int(args.get("max_bytes", 8000))))
        if not path.is_file():
            raise ValueError("文件不存在")
        return {"output": path.read_text(encoding="utf-8", errors="replace")[:max_bytes]}
    if action == "write_file":
        path = _read_path(str(args.get("path", "")), write=True)
        content = str(args.get("content", ""))
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise ValueError("文件超过 2 MiB 限制")
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
        if path.exists():
            backup.write_bytes(path.read_bytes())
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
        shown = path.relative_to(ROOT) if ROOT in path.parents or path == ROOT else path
        return {"output": f"已写入 {shown}（备份 {backup.name}）"}
    if action == "run_command":
        command = str(args.get("command", ""))
        _rate_limit()
        if MODE == "full":
            timeout = max(5.0, min(120.0, float(args.get("timeout", 60))))
            return {"output": _run_shell(command, timeout=timeout)}
        argv = _allowed_command(command)
        return {"output": _run(argv, timeout=300)}
    raise ValueError(f"不支持的操作: {action}")


def handle(request: dict[str, Any]) -> dict[str, Any]:
    expected = _token()
    provided = str(request.get("token", ""))
    if not hmac.compare_digest(expected, provided):
        _audit("auth", {}, False, "invalid token")
        return {"ok": False, "error": "unauthorized"}
    action = str(request.get("action", ""))
    args = request.get("args") if isinstance(request.get("args"), dict) else {}
    if KILL_SWITCH.exists() and action != "status":
        _audit(action, args, False, "kill switch active")
        return {"ok": False, "error": "执行器已被停用（data/admin-executor.disabled 存在）"}
    try:
        result = execute(action, args)
        _audit(action, args, True)
        return {"ok": True, "result": result}
    except Exception as exc:
        _audit(action, args, False, f"{type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}


def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o660)
    try:
        os.chown(SOCKET_PATH, 0, int(os.getenv("CHATBOT_GID", "1000")))
    except (PermissionError, ValueError):
        pass
    server.listen(8)
    while True:
        connection, _ = server.accept()
        with connection:
            try:
                connection.settimeout(10)
                data = b""
                while not data.endswith(b"\n") and len(data) < 4 * 1024 * 1024:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                request = json.loads(data.decode("utf-8") or "{}")
                response = handle(request)
            except Exception as exc:
                response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
            connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
