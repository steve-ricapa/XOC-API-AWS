"""Process-scoped safety guard. Install BEFORE importing XOC. No global OS changes."""
from __future__ import annotations
import builtins
import io
import ipaddress
import logging
import os
from pathlib import Path
import socket
import sys
from contextlib import ExitStack
from unittest.mock import patch

_PACKAGE = Path(__file__).resolve().parent
LAB_ROOT = _PACKAGE / "_lab" if (_PACKAGE / "_artifact.json").is_file() else _PACKAGE.parent
def clean_environment():
    # No inherited app settings, profiles, proxy URLs, cloud tokens or Python paths.
    keep = {k: v for k, v in os.environ.items() if k.upper() in {
        "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP",
        "PROCESSOR_ARCHITECTURE", "PATHEXT"}}
    keep.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
        AWS_EC2_METADATA_DISABLED="true", AWS_DEFAULT_REGION="us-east-1",
        AWS_ACCESS_KEY_ID="LAB_FICTIONAL_NOT_A_CREDENTIAL",
        AWS_SECRET_ACCESS_KEY="LAB_FICTIONAL_NOT_A_CREDENTIAL",
        AWS_SHARED_CREDENTIALS_FILE=str(LAB_ROOT / "NO_CREDENTIALS"),
        AWS_CONFIG_FILE=str(LAB_ROOT / "NO_CONFIG"),
        TICKETS_TABLE_NAME="LAB_TICKETS", REPORTS_TABLE_NAME="LAB_DOCUMENTS",
        REPORTS_STORE_MODE="dynamodb", USER_NOTIFICATION_INBOX_TABLE_NAME="LAB_INBOX",
        POWERTOOLS_LOG_LEVEL="INFO", POWERTOOLS_SERVICE_NAME="xoc-mcp-lab",
        SOPHIA_TOOL_GATEWAY_ENABLED="false", SOPHIA_GATEWAY_REQUIRED="false",
        SOPHIA_THREAD_CAPABILITIES_ENABLED="false", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return keep

class SafeLogs(logging.StreamHandler):
    def emit(self, record):
        # SDK validation warnings may include submitted data. Retain a safe category.
        if record.name.startswith("src.tool_gateway"):
            super().emit(record)
        elif record.name == "xoc_mcp_lab":
            super().emit(record)
        elif record.levelno >= logging.WARNING:
            self.stream.write("lab_dependency_diagnostic suppressed_detail=true\n")
            self.flush()

def configure_logs():
    logging.basicConfig(level=logging.INFO, handlers=[SafeLogs(sys.stderr)], force=True)
    # aws-lambda-powertools defaults to stdout; remove its private stream handler.
    for name in ("service_undefined", "xoc-mcp-lab"):
        log = logging.getLogger(name)
        log.handlers.clear()
        log.propagate = True

class Isolation:
    """Restorable guard; one active instance per process, not a hostile-code sandbox."""
    def __init__(self):
        self.stack = ExitStack()
        self.network_attempts = 0
        self.secret_file_attempts = 0
    def __enter__(self):
        self.stack.enter_context(patch.dict(os.environ, clean_environment(), clear=True))
        self.stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
        def local(address):
            if not isinstance(address, tuple): return False
            try: return ipaddress.ip_address(address[0]).is_loopback
            except ValueError: return False
        def connect(sock, address):
            # Windows asyncio uses an internal loopback socketpair for its wakeup.
            if local(address): return real_connect(sock, address)
            self.network_attempts += 1
            raise RuntimeError("LAB_EXTERNAL_NETWORK_BLOCKED")
        def connect_ex(sock, address):
            if local(address): return real_connect_ex(sock, address)
            self.network_attempts += 1
            raise RuntimeError("LAB_EXTERNAL_NETWORK_BLOCKED")
        real_lookup = socket.getaddrinfo
        def lookup(host, *args, **kwargs):
            if host in ("localhost", "127.0.0.1", "::1"):
                return real_lookup(host, *args, **kwargs)
            self.network_attempts += 1
            raise RuntimeError("LAB_EXTERNAL_DNS_BLOCKED")
        def sendto(*args, **kwargs):
            self.network_attempts += 1
            raise RuntimeError("LAB_DATAGRAM_BLOCKED")
        for target, replacement in (
            ("socket.socket.connect", connect), ("socket.socket.connect_ex", connect_ex),
            ("socket.getaddrinfo", lookup), ("socket.socket.sendto", sendto)):
            self.stack.enter_context(patch(target, replacement))
        def safe_open(original):
            def guarded(file, *args, **kwargs):
                if isinstance(file, (str, bytes, os.PathLike)):
                    path = Path(os.fsdecode(file))
                    parts = {part.lower() for part in path.parts}
                    if (path.name.lower().startswith(".env") or path.name.lower() == "local.settings.json"
                            or parts.intersection({".aws", ".azure", ".ssh"})
                            or path.name in {"NO_CREDENTIALS", "NO_CONFIG"}):
                        self.secret_file_attempts += 1
                        raise RuntimeError("LAB_SECRET_FILE_BLOCKED")
                return original(file, *args, **kwargs)
            return guarded
        for module, attr in ((builtins, "open"), (io, "open"), (os, "open")):
            self.stack.enter_context(patch.object(module, attr, safe_open(getattr(module, attr))))
        return self
    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)

def backend_path(value):
    from .installed import is_installed, require_installed
    if is_installed():
        installed_root = require_installed()
        if value is not None and Path(value).resolve() != installed_root:
            raise ValueError("LAB_NO_CHECKOUT_IN_ARTIFACT_MODE")
        return installed_root
    if value is None:
        raise ValueError("LAB_INSTALLED_ARTIFACT_REQUIRED")
    path = Path(value).resolve(strict=True)
    if path == LAB_ROOT or path in LAB_ROOT.parents or LAB_ROOT.is_relative_to(path):
        raise ValueError("Backend must be the separate reviewed XOC-API-AWS tree")
    for relative in ("src/tool_gateway/executor.py", "src/tool_gateway/policy.py",
                     "src/shared/sophia_tool_chat.py", "src/shared/tickets_store.py"):
        if not (path / relative).is_file(): raise ValueError("Missing required XOC source")
    if path.name != "XOC-API-AWS":
        raise ValueError("Use the reviewed XOC-API-AWS directory, not a release or remote tree")
    return path


def laboratory_python():
    from .installed import is_installed, require_installed
    if is_installed():
        require_installed()
        return Path(sys.executable)
    python = LAB_ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if Path(sys.prefix).resolve() != (LAB_ROOT / ".venv").resolve() or Path(sys.executable).resolve() != python.resolve():
        raise ValueError("LAB_INTERPRETER_REQUIRED")
    return python
