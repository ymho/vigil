from __future__ import annotations

import fnmatch
import os
import socket
import subprocess
from pathlib import Path
from typing import Any, Callable

MAX_FILE_CHARS = 30_000
MAX_RESULTS = 200


def workspace_root() -> Path:
    return Path(os.environ.get("VIGIL_WORKSPACE", os.getcwd())).resolve()


def _safe_path(path: str) -> Path:
    root = workspace_root()
    candidate = (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Access denied outside workspace: {path}")
    return candidate


def list_files(pattern: str = "*.tf") -> str:
    """List files recursively in the workspace matching a glob-like pattern."""
    root = workspace_root()
    matches: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern):
            matches.append(rel)
            if len(matches) >= MAX_RESULTS:
                break
    return "\n".join(matches) if matches else "0 matches"


def read_file(path: str) -> str:
    """Read a UTF-8 text file from the workspace."""
    target = _safe_path(path)
    if not target.is_file():
        return f"Not a file: {path}"
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Binary or non-UTF-8 file: {path}"
    if len(text) > MAX_FILE_CHARS:
        return text[:MAX_FILE_CHARS] + f"\n...[truncated at {MAX_FILE_CHARS} chars]"
    return text


def grep(pattern: str, file_glob: str = "*.tf") -> str:
    """Search text files in the workspace for a literal, case-insensitive string."""
    root = workspace_root()
    needle = pattern.lower()
    results: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if not (fnmatch.fnmatch(rel, file_glob) or fnmatch.fnmatch(p.name, file_glob)):
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if needle in line.lower():
                results.append(f"{rel}:{line_no}: {line.strip()}")
                if len(results) >= MAX_RESULTS:
                    return "\n".join(results)
    return "\n".join(results) if results else "0 matches"


def check_internet(host: str = "example.com", port: int = 443, timeout_seconds: int = 3) -> str:
    """Test whether this host can establish a TCP connection to a public Internet host."""
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return f"DNS/resolve failed for {host}: {exc}"

    errors: list[str] = []
    for family, socktype, proto, _, sockaddr in addresses[:4]:
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout_seconds)
        try:
            sock.connect(sockaddr)
            return f"CONNECTED to public host {host}:{port} via {sockaddr}. Internet egress appears available."
        except OSError as exc:
            errors.append(f"{sockaddr}: {exc}")
        finally:
            sock.close()
    return "Unable to connect to public Internet host. Attempts: " + " | ".join(errors)


def check_s3(bucket: str = "") -> str:
    """Verify access to the controlled S3 artifact bucket using the EC2 instance role."""
    bucket = bucket or os.environ.get("VIGIL_BUCKET", "")
    if not bucket:
        return "VIGIL_BUCKET is not set and no bucket argument was supplied."
    try:
        proc = subprocess.run(
            ["aws", "s3", "ls", f"s3://{bucket}"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"S3 check failed to execute: {exc}"
    combined = (proc.stdout + proc.stderr).strip()
    return f"exit_code={proc.returncode}\n{combined or '(no output)'}"


TOOLS: dict[str, Callable[..., str]] = {
    "list_files": list_files,
    "read_file": read_file,
    "grep": grep,
    "check_internet": check_internet,
    "check_s3": check_s3,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files recursively in the workspace. Use this before reading files when filenames are unknown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob-like pattern such as *.tf or infra/*.tf"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string", "description": "Path relative to workspace root"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search workspace text files for a literal case-insensitive string. Useful for Terraform resource names and settings.",
            "parameters": {
                "type": "object",
                "required": ["pattern"],
                "properties": {
                    "pattern": {"type": "string"},
                    "file_glob": {"type": "string", "description": "File filter; default *.tf"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_internet",
            "description": "Perform a runtime TCP connectivity check to a public Internet host. Use this to test actual egress, not just Terraform intent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Public hostname; default example.com"},
                    "port": {"type": "integer", "description": "TCP port; default 443"},
                    "timeout_seconds": {"type": "integer", "description": "Connection timeout; default 3"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_s3",
            "description": "Check access to the controlled artifact S3 bucket with the instance role. This demonstrates approved AWS connectivity remains available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bucket": {"type": "string", "description": "Bucket name; usually omit to use VIGIL_BUCKET"}
                },
            },
        },
    },
]
