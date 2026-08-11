from __future__ import annotations

import hashlib
import json
import platform
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
    VIGIL Agentが利用できる汎用観測Tool。

    特定の結論を導く専用Toolは持たない。

    Toolは以下の情報を観測するだけ:
    - workspace内のファイル
    - DNS
    - TCP
    - HTTP
    - Linux OSのnetwork情報
    - Linux OSのsystem情報
    - Terraformの静的情報

    Tool自身は最終的な意味判断をしない。
    """

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    # ========================================================
    # File / workspace tools
    # ========================================================

    def list_files(
        self,
        pattern: str = "*",
        max_results: int = 300,
    ) -> str:
        """
        Workspace配下のファイルを再帰的に列挙する。
        """

        max_results = max(
            1,
            min(int(max_results), 2000),
        )

        matches = sorted(
            path
            for path in self.workspace.root.rglob(pattern)
            if path.is_file()
        )

        if not matches:
            return "0 files"

        lines = [
            str(path.relative_to(self.workspace.root))
            for path in matches[:max_results]
        ]

        if len(matches) > max_results:
            lines.append(
                f"[TRUNCATED: showing {max_results} "
                f"of {len(matches)} files]"
            )

        return "\n".join(lines)

    def list_directory(
        self,
        path: str = ".",
    ) -> str:
        """
        指定ディレクトリ直下のファイル・ディレクトリを列挙する。
        """

        target = self.workspace.resolve(path)

        if not target.exists():
            return f"Path not found: {path}"

        if not target.is_dir():
            return f"Not a directory: {path}"

        result: list[str] = []

        for entry in sorted(
            target.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        ):
            suffix = "/" if entry.is_dir() else ""
            result.append(f"{entry.name}{suffix}")

        return "\n".join(result) or "(empty directory)"

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_chars: int = 40000,
    ) -> str:
        """
        Workspace内のテキストファイルを読む。
        行番号付きで返す。
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
            end = max(
                start,
                min(int(end_line), len(lines)),
            )

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
        max_results: int = 200,
    ) -> str:
        """
        Workspace内のファイルを検索する。

        regex=True:
            大文字小文字を無視した正規表現検索

        regex=False:
            大文字小文字を無視した単純文字列検索
        """

        max_results = max(
            1,
            min(int(max_results), 2000),
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
        Workspace内のファイル・ディレクトリの
        種別、サイズ、permissionを取得する。
        """

        target = self.workspace.resolve(path)

        if not target.exists():
            return f"Path not found: {path}"

        stat = target.stat()

        if target == self.workspace.root:
            relative = "."
        else:
            relative = str(
                target.relative_to(
                    self.workspace.root
                )
            )

        result = {
            "path": relative,
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
        Workspace内ファイルのhashを計算する。
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
                chunk = handle.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                digest.update(chunk)

        return (
            f"{algorithm}="
            f"{digest.hexdigest()}"
        )

    # ========================================================
    # DNS / TCP / HTTP
    # ========================================================

    def resolve_dns(
        self,
        host: str,
    ) -> str:
        """
        hostnameを現在のOS resolverで名前解決する。

        DNS解決成功は、その宛先へのTCP/HTTP到達性を
        意味しない。
        """

        try:
            results = socket.getaddrinfo(
                host,
                None,
            )
        except Exception as exc:
            return (
                "scope=OS_DNS_RESOLVER\n"
                "status=ERROR\n"
                f"host={host}\n"
                f"error={type(exc).__name__}: {exc}"
            )

        addresses = sorted(
            {
                item[4][0]
                for item in results
            }
        )

        return "\n".join(
            [
                "scope=OS_DNS_RESOLVER",
                "status=RESOLVED",
                f"host={host}",
                "addresses=" + ",".join(addresses),
                (
                    "note=DNS resolution alone does not "
                    "establish TCP or Internet reachability."
                ),
            ]
        )

    def check_tcp(
        self,
        host: str,
        port: int,
        timeout: int = 5,
    ) -> str:
        """
        任意のhost:portへのTCP接続可否を確認する。

        Internet専用ではない。
        Private IP、localhost、VPC Endpoint等にも使える。
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
                return "\n".join(
                    [
                        "scope=TCP_CONNECTION",
                        "status=CONNECTED",
                        f"host={host}",
                        f"port={port}",
                    ]
                )

        except socket.timeout:
            return "\n".join(
                [
                    "scope=TCP_CONNECTION",
                    "status=TIMEOUT",
                    f"host={host}",
                    f"port={port}",
                ]
            )

        except ConnectionRefusedError:
            return "\n".join(
                [
                    "scope=TCP_CONNECTION",
                    "status=REFUSED",
                    f"host={host}",
                    f"port={port}",
                ]
            )

        except OSError as exc:
            return "\n".join(
                [
                    "scope=TCP_CONNECTION",
                    "status=OS_ERROR",
                    f"host={host}",
                    f"port={port}",
                    (
                        f"error={type(exc).__name__}: "
                        f"{exc}"
                    ),
                ]
            )

        except Exception as exc:
            return "\n".join(
                [
                    "scope=TCP_CONNECTION",
                    "status=ERROR",
                    f"host={host}",
                    f"port={port}",
                    (
                        f"error={type(exc).__name__}: "
                        f"{exc}"
                    ),
                ]
            )

    def http_request(
        self,
        url: str,
        method: str = "HEAD",
        timeout: int = 10,
        max_bytes: int = 4096,
    ) -> str:
        """
        HTTP/HTTPS endpointへHEADまたはGETを送る。

        外部Internet専用ではなく、
        Private Endpointやlocalhostにも利用できる。
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

                result: dict[str, Any] = {
                    "scope": "HTTP_REQUEST",
                    "status": "SUCCESS",
                    "http_status": response.status,
                    "url": response.geturl(),
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
                    "scope": "HTTP_REQUEST",
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
                    "scope": "HTTP_REQUEST",
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
    # Linux network observation
    # ========================================================

    def get_os_routes(
        self,
    ) -> str:
        """
        Linux OSが認識しているrouting tableを取得する。

        IMPORTANT:
        これはゲストOSのrouting情報である。
        AWS VPC Route Table、Internet Gateway、NAT Gateway、
        Transit Gateway等のクラウド側routing resourceの
        存在を直接示すものではない。
        """

        output = self._run_readonly(
            [
                "ip",
                "route",
                "show",
            ]
        )

        return "\n".join(
            [
                "scope=LINUX_OS_ROUTING_TABLE",
                (
                    "note=This is the guest OS routing "
                    "table only. It does not directly "
                    "describe AWS VPC route tables or "
                    "Internet/NAT gateways."
                ),
                output,
            ]
        )

    def get_os_rules(
        self,
    ) -> str:
        """
        Linux OSのpolicy routing ruleを取得する。
        """

        output = self._run_readonly(
            [
                "ip",
                "rule",
                "show",
            ]
        )

        return "\n".join(
            [
                "scope=LINUX_OS_POLICY_ROUTING",
                output,
            ]
        )

    def get_interfaces(
        self,
    ) -> str:
        """
        Linux OSのnetwork interfaceとIPを取得する。
        """

        output = self._run_readonly(
            [
                "ip",
                "-brief",
                "address",
            ]
        )

        return "\n".join(
            [
                "scope=LINUX_OS_INTERFACES",
                output,
            ]
        )

    def get_neighbor_table(
        self,
    ) -> str:
        """
        Linux OSのneighbor tableを取得する。
        """

        output = self._run_readonly(
            [
                "ip",
                "neighbor",
                "show",
            ]
        )

        return "\n".join(
            [
                "scope=LINUX_OS_NEIGHBOR_TABLE",
                output,
            ]
        )

    def get_listening_ports(
        self,
    ) -> str:
        """
        Linux上でLISTENしているTCP/UDP socketを取得する。
        """

        output = self._run_readonly(
            [
                "ss",
                "-lntup",
            ]
        )

        return "\n".join(
            [
                "scope=LINUX_OS_LISTENING_SOCKETS",
                output,
            ]
        )

    # ========================================================
    # OS / runtime observation
    # ========================================================

    def get_hostname(
        self,
    ) -> str:
        """
        現在のhost nameを取得する。
        """

        return "\n".join(
            [
                "scope=LOCAL_SYSTEM",
                f"hostname={socket.gethostname()}",
            ]
        )

    def get_system_info(
        self,
    ) -> str:
        """
        現在のOS / architecture情報を取得する。
        """

        result = {
            "scope": "LOCAL_SYSTEM",
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        }

        return json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )

    # ========================================================
    # Terraform observation
    # ========================================================

    def terraform_validate(
        self,
    ) -> str:
        """
        workspace内のTerraform configurationをvalidateする。

        apply / plan / state mutationは行わない。
        """

        return self._run_readonly(
            [
                "terraform",
                "-chdir="
                + str(self.workspace.root),
                "validate",
                "-no-color",
            ],
            timeout=60,
        )

    def terraform_state_list(
        self,
    ) -> str:
        """
        workspaceにTerraform stateが存在する場合、
        state内のresource addressを列挙する。

        stateを変更しない。
        """

        return self._run_readonly(
            [
                "terraform",
                "-chdir="
                + str(self.workspace.root),
                "state",
                "list",
            ],
            timeout=30,
        )

    def terraform_state_show(
        self,
        address: str,
    ) -> str:
        """
        Terraform state内の1 resourceを表示する。

        stateを変更しない。
        """

        if not re.fullmatch(
            r"[A-Za-z0-9_\-\.\[\]\"']+",
            address,
        ):
            return (
                "ERROR: invalid Terraform "
                "resource address"
            )

        return self._run_readonly(
            [
                "terraform",
                "-chdir="
                + str(self.workspace.root),
                "state",
                "show",
                "-no-color",
                address,
            ],
            timeout=30,
        )

    # ========================================================
    # Internal read-only process runner
    # ========================================================

    def _run_readonly(
        self,
        command: list[str],
        timeout: int = 20,
    ) -> str:
        """
        Tool内部専用。

        任意commandをLLMから直接指定させない。
        """

        timeout = max(
            1,
            min(int(timeout), 60),
        )

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        except subprocess.TimeoutExpired:
            return "\n".join(
                [
                    "status=TIMEOUT",
                    (
                        "command="
                        + json.dumps(command)
                    ),
                ]
            )

        except FileNotFoundError:
            return "\n".join(
                [
                    "status=NOT_FOUND",
                    f"program={command[0]}",
                ]
            )

        except Exception as exc:
            return "\n".join(
                [
                    "status=ERROR",
                    (
                        f"error={type(exc).__name__}: "
                        f"{exc}"
                    ),
                ]
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
            "get_os_routes": self.get_os_routes,
            "get_os_rules": self.get_os_rules,
            "get_interfaces": self.get_interfaces,
            "get_neighbor_table": (
                self.get_neighbor_table
            ),
            "get_listening_ports": (
                self.get_listening_ports
            ),
            "get_hostname": self.get_hostname,
            "get_system_info": (
                self.get_system_info
            ),
            "terraform_validate": (
                self.terraform_validate
            ),
            "terraform_state_list": (
                self.terraform_state_list
            ),
            "terraform_state_show": (
                self.terraform_state_show
            ),
        }


# ============================================================
# Tool schemas exposed to LLM
# ============================================================


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "Workspace配下のファイルを再帰的に列挙する。"
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
                "Workspace内の指定ディレクトリ直下を一覧表示する。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Workspace内のテキストファイルを行番号付きで読む。"
            ),
            "parameters": {
                "type": "object",
                "required": [
                    "path",
                ],
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
                "正規表現または単純文字列検索に対応。"
            ),
            "parameters": {
                "type": "object",
                "required": [
                    "pattern",
                ],
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
                "Workspace内のfile/directoryの"
                "種別、size、permissionを取得する。"
            ),
            "parameters": {
                "type": "object",
                "required": [
                    "path",
                ],
                "properties": {
                    "path": {
                        "type": "string",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hash_file",
            "description": (
                "Workspace内ファイルのhashを計算する。"
            ),
            "parameters": {
                "type": "object",
                "required": [
                    "path",
                ],
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
                "現在のOS resolverでhostnameをDNS解決する。"
                "DNS解決結果のみを観測するTool。"
            ),
            "parameters": {
                "type": "object",
                "required": [
                    "host",
                ],
                "properties": {
                    "host": {
                        "type": "string",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_tcp",
            "description": (
                "任意のhost:portへのTCP接続可否を確認する。"
                "Internet専用ではない汎用TCP疎通Tool。"
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
                "任意のHTTP/HTTPS endpointへ"
                "HEADまたはGET requestを送る。"
            ),
            "parameters": {
                "type": "object",
                "required": [
                    "url",
                ],
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
            "name": "get_os_routes",
            "description": (
                "LinuxゲストOSが認識しているrouting tableを取得する。"
                "これはOS内のrouting情報であり、"
                "AWS VPC Route TableやInternet/NAT Gateway等の"
                "クラウド側resourceを直接示すものではない。"
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
            "name": "get_os_rules",
            "description": (
                "LinuxゲストOSのpolicy routing ruleを取得する。"
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
                "LinuxゲストOSのnetwork interfaceと"
                "IP addressを取得する。"
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
            "name": "get_neighbor_table",
            "description": (
                "LinuxゲストOSのneighbor tableを取得する。"
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
                "Linux上でLISTEN中のTCP/UDP socketを取得する。"
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
            "name": "get_hostname",
            "description": (
                "現在のhost nameを取得する。"
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
            "name": "get_system_info",
            "description": (
                "現在のOS、architecture、Python versionを取得する。"
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
            "name": "terraform_validate",
            "description": (
                "現在のworkspaceのTerraform configurationを"
                "read-onlyでvalidateする。"
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
            "name": "terraform_state_list",
            "description": (
                "Terraform stateが存在する場合、"
                "state内resource addressをread-onlyで列挙する。"
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
            "name": "terraform_state_show",
            "description": (
                "Terraform state内の指定resourceをread-onlyで表示する。"
            ),
            "parameters": {
                "type": "object",
                "required": [
                    "address",
                ],
                "properties": {
                    "address": {
                        "type": "string",
                    },
                },
            },
        },
    },
]