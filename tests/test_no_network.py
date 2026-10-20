"""No shipped file can open a network connection: checked, not promised.

Every Python file in the package (and the in-development desktop core) is
read as code and refused if it imports anything that can open a connection.
A redaction tool that never touches the network cannot leak through it, and
anyone can confirm this by reading one test.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = sorted(list((ROOT / "inkguard").glob("*.py")) + [ROOT / "desktop" / "opal_service.py"])

#: Modules that can reach another computer.
NETWORK = {"socket", "ssl", "http", "urllib.request", "urllib3", "httpx", "requests", "aiohttp",
           "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc", "websocket",
           "websockets", "asyncio.streams", "fastapi", "uvicorn", "flask", "socketserver"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found |= {f"{node.module}.{a.name}" for a in node.names}
    return found


def _is_network(name: str) -> bool:
    return any(name == n or name.startswith(n + ".") for n in NETWORK)


#: The one exception, and why: the self-test imports socket only to switch
#: every connection OFF while it runs the engine. The test below checks that
#: this file never calls anything that connects.
BLOCKS_ONLY = {"inkguard/fluorite.py": {"socket"}}


def test_the_self_test_only_switches_the_network_off():
    tree = ast.parse((ROOT / "inkguard" / "fluorite.py").read_text(encoding="utf-8"))
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not {c for c in calls if c.startswith("socket.")}, calls
    assigned = {ast.unparse(t) for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets}
    assert {"socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"} <= assigned


def test_nothing_that_ships_imports_a_network_module():
    offenders = {}
    for path in SHIPPED:
        allowed = BLOCKS_ONLY.get(path.relative_to(ROOT).as_posix(), set())
        bad = sorted(n for n in _imports(path) if _is_network(n) and n not in allowed)
        if bad:
            offenders[str(path.relative_to(ROOT))] = bad
    assert offenders == {}, f"network code found: {offenders}"


def test_the_check_itself_would_catch_one(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("import urllib.request\nfrom socket import create_connection\n")
    assert {n for n in _imports(f) if _is_network(n)} == {
        "urllib.request", "socket", "socket.create_connection"}


_OFFLINE_RUN = r'''
import socket, sys
attempts = []
def _refuse(*a, **k):
    attempts.append(repr(a[:2]))
    raise OSError("network is switched off for this test")
socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse
socket.getaddrinfo = _refuse
from inkguard import contract
r = contract.deidentify(
    "Seen by Dr. Okafor. Email maria.lopez@example.org, site www.lopezfamily.example.",
    mode="mask")
print("ATTEMPTS", len(attempts), attempts[:3])
print("CLEAN", r.clean_text if hasattr(r, "clean_text") else r)
'''


def test_the_engine_runs_with_the_network_switched_off(tmp_path):
    """Checking our own imports is not enough: a library we use (tldextract,
    through Presidio's email check) downloaded a list from the internet on
    first use. Run the real engine with every connection refused and a fresh
    home folder, and require that nothing even tried to connect."""
    import os
    import subprocess
    import sys
    env = dict(os.environ, HOME=str(tmp_path), XDG_CACHE_HOME=str(tmp_path / "cache"),
               TLDEXTRACT_CACHE=str(tmp_path / "tld"))
    out = subprocess.run([sys.executable, "-c", _OFFLINE_RUN], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "ATTEMPTS 0 " in out.stdout, out.stdout + out.stderr[-2000:]
    assert "maria.lopez@example.org" not in out.stdout
    assert not any(tmp_path.rglob("*.json")), "a cache file was written to the home folder"


def test_the_check_covers_the_whole_package():
    assert len(SHIPPED) > 30 and all(p.exists() for p in SHIPPED)
