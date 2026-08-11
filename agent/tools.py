from __future__ import annotations

import re
import socket
import subprocess
from pathlib import Path
from typing import Callable


class Workspace:
    """Toolがアクセスできる範囲をworkspace配下に制限する。"""

    def __init__(self, root: str):
        self.root = Path(root).resolve()

        if not self.root.exists():
            raise ValueError(f"Workspace does not exist: {self.root}")

    def resolve(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()

        if target == self.root:
            return target

        if self.root not in target.parents:
            raise ValueError(
                f"Access outside workspace denied: {relative_path}"
            )

        return target


class Tools:
    """VIGIL Agentが利用できるTool群。"""

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def list_files(self, pattern: str = "*.tf") -> str:
        """List files in the workspace matching a glob pattern."""

        files = sorted(
            p
            for p in self.workspace.root.rglob(pattern)
            if p.is_file()
        )

        if not files:
            return "0 files"

        return "\n".join(
            str(p.relative_to(self.workspace.root))
            for p in files
        )

    def read_file(self, path: str) -> str:
        """Read a text file from the workspace."""

        target = self.workspace.resolve(path)

        if not target.is_file():
            return f"File not found: {path}"

        try:
            text = target.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"

        # LLMへ巨大なファイルを丸ごと渡さない
        max_chars = 30_000

        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[TRUNCATED]"

        return text

    def grep(
        self,
        pattern: str,
        glob: str = "*.tf",
    ) -> str:
        """Search workspace files using a regular expression."""

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return f"ERROR: invalid regex: {exc}"

        results: list[str] = []

        for path in sorted(self.workspace.root.rglob(glob)):
            if not path.is_file():
                continue

            try:
                lines = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()
            except Exception:
                continue

            for lineno, line in enumerate(lines, 1):
                if not regex.search(line):
                    continue

                relative = path.relative_to(self.workspace.root)

                results.append(
                    f"{relative}:{lineno}: {line.strip()}"
                )

                if len(results) >= 100:
                    results.append("[TRUNCATED: 100 matches]")
                    return "\n".join(results)

        return "\n".join(results) or "0 matches"

    def check_internet(
        self,
        host: str = "1.1.1.1",
        port: int = 443,
        timeout: int = 5,
    ) -> str:
        """
        Test actual outbound Internet connectivity.

        DNSを使わずInternet側IPへ直接TCP接続することで、
        名前解決失敗とInternet到達不能を切り分ける。
        """

        try:
            with socket.create_connection(
                (host, int(port)),
                timeout=int(timeout),
            ):
                return f"CONNECTED: {host}:{port}"

        except Exception as exc:
            return (
                f"UNREACHABLE: {host}:{port} "
                f"({type(exc).__name__}: {exc})"
            )

    def check_s3(self, bucket: str) -> str:
        """
        Verify access to the VIGIL S3 bucket.

        全Bucket一覧ではなく、指定Bucketへのアクセスだけを確認する。
        """

        try:
            proc = subprocess.run(
                [
                    "aws",
                    "s3",
                    "ls",
                    f"s3://{bucket}/",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"

        output = (
            proc.stdout.strip()
            or proc.stderr.strip()
            or "(no output)"
        )

        return (
            f"exit_code={proc.returncode}\n"
            f"{output}"
        )

    def registry(self) -> dict[str, Callable]:
        """Agent Runtimeから呼び出すTool一覧。"""

        return {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "grep": self.grep,
            "check_internet": self.check_internet,
            "check_s3": self.check_s3,
        }


#
# LLMへ渡す「Toolの説明書」
#
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "Workspace内のファイルを検索する。"
                "Terraformファイルの所在が分からない場合は"
                "最初にこのToolを使う。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "例: *.tf",
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
                "Workspace内の指定ファイルを読み取る。"
            ),
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Workspaceからの相対パス"
                        ),
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Workspace内のファイルから文字列を検索する。"
                "Terraformのresourceや設定を探す場合に使う。"
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
                        "description": "例: *.tf",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_internet",
            "description": (
                "EC2からInternet側IPへのTCP接続を試し、"
                "実際のInternet到達性を確認する。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "既定値: 1.1.1.1",
                    },
                    "port": {
                        "type": "integer",
                        "description": "既定値: 443",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "既定値: 5秒",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_s3",
            "description": (
                "指定したS3 Bucketへ、EC2のIAM Roleと"
                "VPC Endpoint経由でアクセスできるか確認する。"
            ),
            "parameters": {
                "type": "object",
                "required": ["bucket"],
                "properties": {
                    "bucket": {
                        "type": "string",
                    }
                },
            },
        },
    },
]