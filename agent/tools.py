from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


# ============================================================
# Workspace
# ============================================================


class Workspace:
    """
    Toolからアクセス可能なファイル範囲をworkspace配下に制限する。
    """

    def __init__(self, root: str):
        self.root = Path(root).resolve()

        if not self.root.exists():
            raise ValueError(
                f"Workspace does not exist: {self.root}"
            )

        if not self.root.is_dir():
            raise ValueError(
                f"Workspace is not a directory: {self.root}"
            )

    def resolve(self, relative_path: str) -> Path:
        """
        Workspaceからの相対パスを安全に解決する。

        ../ 等によるworkspace外アクセスは禁止。
        """

        if relative_path in ("", "."):
            return self.root

        target = (self.root / relative_path).resolve()

        if target == self.root:
            return target

        if self.root not in target.parents:
            raise ValueError(
                f"Access outside workspace denied: {relative_path}"
            )

        return target


# ============================================================
# Tools
# ============================================================


class Tools:
    """
    VIGIL Agentが利用可能な汎用Tool群。

    特定の調査テーマ専用ではなく、
    ファイル・ネットワーク・OSを観測するための
    基本的な道具だけを提供する。
    """

    ALLOWED_PROGRAMS = {
        "ip",
        "ss",
        "getent",
        "hostname",
        "uname",
        "terraform",
        "aws",
    }

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    # ========================================================
    # File tools
    # ========================================================

    def list_files(
        self,
        pattern: str = "*",
        max_results: int = 200,
    ) -> str:
        """
        Workspace配下のファイルをglobで列挙する。
        """

        max_results = max(1, min(int(max_results), 1000))

        matches = sorted(
            path
            for path in self.workspace.root.rglob(pattern)
            if path.is_file()
        )

        if not matches:
            return "0 files"

        result = [
            str(path.relative_to(self.workspace.root))
            for path in matches[:max_results]
        ]

        if len(matches) > max_results:
            result.append(
                f"[TRUNCATED: showing {max_results} of "
                f"{len(matches)} files]"
            )

        return "\n".join(result)

    def list_directory(
        self,
        path: str = ".",
    ) -> str:
        """
        指定ディレクトリ直下の内容を表示する。
        """

        target = self.workspace.resolve(path)

        if not target.exists():
            return f"Path not found: {path}"

        if not target.is_dir():
            return f"Not a directory: {path}"

        entries: list[str] = []

        for entry in sorted(
            target.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        ):
            suffix = "/" if entry.is_dir() else ""
            entries.append(f"{entry.name}{suffix}")

        return "\n".join(entries) or "(empty directory)"

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_chars: int = 30000,
    ) -> str:
        """
        Workspace内のテキストファイルを読み取る。
        行範囲指定可能。
        """

        target = self.workspace.resolve(path)

        if not target.exists():
            return f"File not found: {path}"

        if not target.is_file():
            return f"Not a file: {path}"

        try:
            text = target.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            return (
                f"ERROR: {type(exc).__name__}: {exc}"
            )

        lines = text.splitlines()

        start = max(1, int(start_line))

        if end_line is None:
            end = len(lines)
        else:
            end = max(start, int(end_line))

        selected = lines[start - 1:end]

        result = "\n".join(
            f"{number}: {line}"
            for number, line in enumerate(
                selected,
                start=start,
            )
        )

        max_chars = max(
            1000,
            min(int(max_chars), 100000),
        )

        if len(result) > max_chars:
            result = (
                result[:max_chars]
                + "\n[TRUNCATED]"
            )

        return result

    def search_text(
        self,
        pattern: str,
        glob: str = "*",
        regex: bool = True,
        max_results: int = 100,
    ) -> str:
        """
        Workspace内のファイルを検索する。

        regex=True:
          正規表現検索

        regex=False:
          大文字小文字を無視した単純文字列検索
        """

        max_results = max(
            1,
            min(int(max_results), 1000),
        )

        compiled = None

        if regex:
            try:
                compiled = re.compile(
                    pattern,
                    re.IGNORECASE,
                )
            except re.error as exc:
                return (
                    f"ERROR: invalid regex: {exc}"
                )

        results: list[str] = []

        for file in sorted(
            self.workspace.root.rglob(glob)
        ):
            if not file.is_file():
                continue

            try:
                lines = file.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()
            except Exception:
                continue

            for lineno, line in enumerate(
                lines,
                start=1,
            ):
                if regex:
                    matched = bool(
                        compiled.search(line)
                    )
                else:
                    matched = (
                        pattern.lower()
                        in line.lower()
                    )

                if not matched:
                    continue

                relative = file.relative_to(
                    self.workspace.root
                )

                results.append(
                    f"{relative}:{lineno}: "
                    f"{line.strip()}"
                )

                if len(results) >= max_results:
                    results.append(
                        f"[TRUNCATED: "
                        f"{max_results} matches]"
                    )
                    return "\n".join(results)

        return "\n".join(results) or "0 matches"

    def file_stat(
        self,
        path: str,
    ) -> str:
        """
        ファイルまたはディレクトリの基本情報を取得する。
        """

        target = self.workspace.resolve(path)

        if not target.exists():
            return f"Path not found: {path}"

        stat = target.stat()

        result = {
            "path": str(
                target.relative_to(
                    self.workspace.root
                )
            )
            if target != self.workspace.root
            else ".",
            "type": (
                "directory"
                if target.is_dir()
                else "file"
            ),
            "size_bytes": stat.st_size,
            "mode": oct(stat.st_mode & 0o777),
        }

        return json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )

    def hash_file(
        self,
        path: str,
        algorithm: str = "sha256",
    ) -> str:
        """
        ファイルのハッシュ値を取得する。
        """

        target = self.workspace.resolve(path)

        if not target.is_file():
            return f"File not found: {path}"

        allowed = {
            "sha256",
            "sha512",
            "md5",
        }

        algorithm = algorithm.lower()

        if algorithm not in allowed:
            return (
                "ERROR: unsupported algorithm. "
                "Allowed: sha256, sha512, md5"
            )

        digest = hashlib.new(algorithm)

        with target.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)

                if not chunk:
                    break

                digest.update(chunk)

        return (
            f"{algorithm}="
            f"{digest.hexdigest()}"
        )

    # ========================================================
    # Network tools
    # ========================================================

    def resolve_dns(
        self,
        host: str,
    ) -> str:
        """
        DNS名前解決を行う。
        """

        try:
            results = socket.getaddrinfo(
                host,
                None,
            )
        except Exception as exc:
            return (
                f"DNS_ERROR: "
                f"{type(exc).__name__}: {exc}"
            )

        addresses = sorted(
            {
                item[4][0]
                for item in results
            }
        )

        return "\n".join(addresses)

    def check_tcp(
        self,
        host: str,
        port: int,
        timeout: int = 5,
    ) -> str:
        """
        指定したhost:portへのTCP接続を試す。

        Internet専用ではない。
        任意のTCP endpointへの到達性確認に使用する。
        """

        port = int(port)
        timeout = max(
            1,
            min(int(timeout), 30),
        )

        try:
            with socket.create_connection(
                (host, port),
                timeout=timeout,
            ):
                return (
                    "status=CONNECTED\n"
                    f"host={host}\n"
                    f"port={port}"
                )

        except socket.timeout:
            return (
                "status=TIMEOUT\n"
                f"host={host}\n"
                f"port={port}"
            )

        except ConnectionRefusedError:
            return (
                "status=REFUSED\n"
                f"host={host}\n"
                f"port={port}"
            )

        except Exception as exc:
            return (
                "status=ERROR\n"
                f"host={host}\n"
                f"port={port}\n"
                f"error={type(exc).__name__}: {exc}"
            )

    def http_request(
        self,
        url: str,
        method: str = "HEAD",
        timeout: int = 10,
        max_bytes: int = 4096,
    ) -> str:
        """
        HTTP/HTTPS requestを実行する。

        HEADまたはGETのみ。
        """

        parsed = urllib.parse.urlparse(url)

        if parsed.scheme not in {
            "http",
            "https",
        }:
            return (
                "ERROR: only http and https "
                "URLs are allowed"
            )

        method = method.upper()

        if method not in {
            "HEAD",
            "GET",
        }:
            return (
                "ERROR: only HEAD and GET "
                "are allowed"
            )

        timeout = max(
            1,
            min(int(timeout), 30),
        )

        max_bytes = max(
            0,
            min(int(max_bytes), 65536),
        )

        request = urllib.request.Request(
            url,
            method=method,
            headers={
                "User-Agent": "VIGIL-Agent/1.0",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:

                body = b""

                if method == "GET":
                    body = response.read(
                        max_bytes
                    )

                result = {
                    "status": "SUCCESS",
                    "http_status": response.status,
                    "url": response.geturl(),
                    "headers": dict(
                        response.headers.items()
                    ),
                }

                if body:
                    result["body_preview"] = (
                        body.decode(
                            "utf-8",
                            errors="replace",
                        )
                    )

                return json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                )

        except urllib.error.HTTPError as exc:
            return json.dumps(
                {
                    "status": "HTTP_ERROR",
                    "http_status": exc.code,
                    "url": url,
                },
                ensure_ascii=False,
                indent=2,
            )

        except Exception as exc:
            return json.dumps(
                {
                    "status": "ERROR",
                    "url": url,
                    "error": (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )

    # ========================================================
    # OS observation tools
    # ========================================================

    def get_routes(self) -> str:
        """
        OSが認識しているIP routeを表示する。
        """

        return self._run_program_internal(
            "ip",
            ["route", "show"],
            timeout=10,
        )

    def get_interfaces(self) -> str:
        """
        OSのnetwork interface / IP addressを表示する。
        """

        return self._run_program_internal(
            "ip",
            ["-brief", "address"],
            timeout=10,
        )

    def get_listening_ports(self) -> str:
        """
        OS上のlisten socketを表示する。
        """

        return self._run_program_internal(
            "ss",
            ["-lntup"],
            timeout=10,
        )

    # ========================================================
    # Generic read-only program tool
    # ========================================================

    def run_program(
        self,
        program: str,
        args: list[str] | None = None,
        timeout: int = 30,
    ) -> str:
        """
        allow-listされたコマンドをshell無しで実行する。

        任意shell実行は禁止。
        """

        if args is None:
            args = []

        if program not in self.ALLOWED_PROGRAMS:
            return (
                f"ERROR: program not allowed: "
                f"{program}"
            )

        return self._run_program_internal(
            program,
            args,
            timeout,
        )

    def _run_program_internal(
        self,
        program: str,
        args: list[str],
        timeout: int,
    ) -> str:

        timeout = max(
            1,
            min(int(timeout), 60),
        )

        command = [
            program,
            *[str(arg) for arg in args],
        ]

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        except subprocess.TimeoutExpired:
            return (
                "status=TIMEOUT\n"
                f"command={json.dumps(command)}"
            )

        except FileNotFoundError:
            return (
                "status=NOT_FOUND\n"
                f"program={program}"
            )

        except Exception as exc:
            return (
                "status=ERROR\n"
                f"error={type(exc).__name__}: "
                f"{exc}"
            )

        stdout = process.stdout.strip()
        stderr = process.stderr.strip()

        return "\n".join(
            [
                f"exit_code={process.returncode}",
                f"stdout={stdout or '(empty)'}",
                f"stderr={stderr or '(empty)'}",
            ]
        )

    # ========================================================
    # Registry
    # ========================================================

    def registry(
        self,
    ) -> dict[str, Callable[..., Any]]:
        """
        Agent Runtimeから呼べるTool一覧。
        """

        return {
            "list_files": self.list_files,
            "list_directory": self.list_directory,
            "read_file": self.read_file,
            "search_text": self.search_text,
            "file_stat": self.file_stat,
            "hash_file": self.hash_file,
            "resolve_dns": self.resolve_dns,
            "check_tcp": self.check_tcp,
            "http_request": self.http_request,
            "get_routes": self.get_routes,
            "get_interfaces": self.get_interfaces,
            "get_listening_ports": (
                self.get_listening_ports
            ),
            "run_program": self.run_program,
        }


# ============================================================
# Tool schemas exposed to the LLM
# ============================================================


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "Workspace配下のファイルをglobパターンで列挙する。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                    },
                    "max_results": {
                        "type": "integer",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "Workspace内のディレクトリ直下を一覧表示する。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Workspace内のテキストファイルを読む。"
                "必要に応じて行範囲を指定できる。"
            ),
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {
                        "type": "string",
                    },
                    "start_line": {
                        "type": "integer",
                    },
                    "end_line": {
                        "type": "integer",
                    },
                    "max_chars": {
                        "type": "integer",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": (
                "Workspace内のテキストを検索する。"
                "正規表現または単純文字列検索に対応する。"
            ),
            "parameters": {
                "type": "object",
                "required": ["pattern"],
                "properties": {
                    "pattern": {
                        "type": "string",
                    },
                    "glob": {
                        "type": "string",
                    },
                    "regex": {
                        "type": "boolean",
                    },
                    "max_results": {
                        "type": "integer",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_stat",
            "description": (
                "ファイルまたはディレクトリの"
                "サイズ・種別・権限を取得する。"
            ),
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {
                        "type": "string",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hash_file",
            "description": (
                "Workspace内ファイルのハッシュ値を計算する。"
            ),
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {
                        "type": "string",
                    },
                    "algorithm": {
                        "type": "string",
                        "enum": [
                            "sha256",
                            "sha512",
                            "md5",
                        ],
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_dns",
            "description": (
                "hostnameをDNS名前解決する。"
            ),
            "parameters": {
                "type": "object",
                "required": ["host"],
                "properties": {
                    "host": {
                        "type": "string",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_tcp",
            "description": (
                "指定したhostとTCP portへの"
                "接続可否を確認する汎用疎通Tool。"
            ),
            "parameters": {
                "type": "object",
                "required": [
                    "host",
                    "port",
                ],
                "properties": {
                    "host": {
                        "type": "string",
                    },
                    "port": {
                        "type": "integer",
                    },
                    "timeout": {
                        "type": "integer",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": (
                "HTTPまたはHTTPS endpointへ"
                "HEAD/GET requestを送る。"
            ),
            "parameters": {
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {
                        "type": "string",
                    },
                    "method": {
                        "type": "string",
                        "enum": [
                            "HEAD",
                            "GET",
                        ],
                    },
                    "timeout": {
                        "type": "integer",
                    },
                    "max_bytes": {
                        "type": "integer",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_routes",
            "description": (
                "現在のOS routing tableを表示する。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_interfaces",
            "description": (
                "現在のnetwork interfaceと"
                "IP addressを表示する。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_listening_ports",
            "description": (
                "OS上でlistenしている"
                "network socketを表示する。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_program",
            "description": (
                "allow-listされた読取系programを"
                "shellを介さず実行する。"
                "利用可能: ip, ss, getent, hostname, "
                "uname, terraform, aws"
            ),
            "parameters": {
                "type": "object",
                "required": ["program"],
                "properties": {
                    "program": {
                        "type": "string",
                        "enum": [
                            "ip",
                            "ss",
                            "getent",
                            "hostname",
                            "uname",
                            "terraform",
                            "aws",
                        ],
                    },
                    "args": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                    },
                    "timeout": {
                        "type": "integer",
                    },
                },
            },
        },
    },
]