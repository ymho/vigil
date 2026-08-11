#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from tools import TOOL_SCHEMAS, Tools, Workspace


DEFAULT_MODEL = "vigil-qwen3"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 600
DEFAULT_MAX_TURNS = 15


SYSTEM_PROMPT = """
あなたはVIGIL閉域環境で動作する調査エージェントです。

目的を達成するために、利用可能なToolを自分で選択できます。
Toolの実行結果を確認し、必要であれば次のToolを選択してください。

ルール:
- 推測だけで結論を出さない。
- Terraform構成を調査するときはToolを使う。
- ファイルの所在が不明ならlist_filesを使う。
- Terraform resourceや設定を探すならgrepを使う。
- 必要なファイルだけread_fileする。
- 実際のInternet到達性を調べる場合はcheck_internetを使う。
- Toolの結果を確認してから次の行動を判断する。
- 十分な根拠が集まったらToolを呼ばずに最終回答する。
- 最終回答では、確認できた事実と推測を区別する。
""".strip()


class Logger:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet

    def log(self, message: str = "") -> None:
        if not self.quiet:
            print(message, flush=True)

    def section(self, title: str) -> None:
        if self.quiet:
            return

        print(flush=True)
        print(f"=== {title} ===", flush=True)

    def llm(self, message: str) -> None:
        self.log(f"[LLM] {message}")

    def tool(self, message: str) -> None:
        self.log(f"[TOOL] {message}")

    def result(self, message: str) -> None:
        self.log(f"[RESULT] {message}")


class ProgressReporter:
    """
    Ollamaの応答待ち中に一定間隔で経過時間を表示する。
    """

    def __init__(
        self,
        logger: Logger,
        message: str = "次の行動を判断中",
        interval: int = 10,
    ):
        self.logger = logger
        self.message = message
        self.interval = interval

        self.started_at = 0.0
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            elapsed = int(time.time() - self.started_at)

            self.logger.llm(
                f"{self.message}... {elapsed}s"
            )

    def __enter__(self):
        self.started_at = time.time()

        self.logger.llm(
            f"{self.message}..."
        )

        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
        )
        self.thread.start()

        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        self.stop_event.set()

        if self.thread:
            self.thread.join(timeout=1)

        elapsed = time.time() - self.started_at

        if exc_type is None:
            self.logger.llm(
                f"応答受信 ({elapsed:.1f}s)"
            )

        return False


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: int,
        logger: Logger,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.logger = logger

    def chat(
        self,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Ollama /api/chat を呼ぶ。
        """

        payload = {
            "model": self.model,
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "stream": False,

            # Agent Loop中に毎回モデルをロードし直さない
            "keep_alive": "30m",
        }

        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with ProgressReporter(
                logger=self.logger,
                interval=10,
            ):
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                ) as response:
                    body = response.read()

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(
                "utf-8",
                errors="replace",
            )

            raise RuntimeError(
                f"Ollama HTTP {exc.code}: {detail}"
            ) from exc

        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
        ) as exc:
            raise RuntimeError(
                f"Ollama request failed: {exc}"
            ) from exc

        try:
            return json.loads(body)

        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Ollama returned invalid JSON"
            ) from exc


class AgentRuntime:
    """
    LLMとToolを接続し、目的達成までLoopさせる。
    """

    def __init__(
        self,
        ollama: OllamaClient,
        tools: Tools,
        logger: Logger,
        max_turns: int,
    ):
        self.ollama = ollama
        self.tools = tools
        self.logger = logger
        self.max_turns = max_turns

        self.tool_registry = tools.registry()

    def run(self, prompt: str) -> str:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        self.logger.section("VIGIL AGENT START")
        self.logger.log(
            f"Model     : {self.ollama.model}"
        )
        self.logger.log(
            f"Workspace : {self.tools.workspace.root}"
        )
        self.logger.log(
            f"Max turns : {self.max_turns}"
        )

        for turn in range(
            1,
            self.max_turns + 1,
        ):
            self.logger.section(
                f"TURN {turn}/{self.max_turns}"
            )

            #
            # 1. LLMに「次に何をするか」を判断させる
            #
            response = self.ollama.chat(messages)

            message = response.get(
                "message",
                {},
            )

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": message.get(
                    "content",
                    "",
                ),
            }

            tool_calls = message.get(
                "tool_calls",
                [],
            )

            if tool_calls:
                assistant_message["tool_calls"] = (
                    tool_calls
                )

            messages.append(
                assistant_message
            )

            #
            # 2. Tool指定がなければ最終回答
            #
            if not tool_calls:
                answer = (
                    message.get(
                        "content",
                        "",
                    ).strip()
                )

                self.logger.llm(
                    "Tool呼び出しなし → 任務完了"
                )

                self.logger.section(
                    "FINAL ANSWER"
                )

                print(
                    answer,
                    flush=True,
                )

                self._print_metrics(
                    response
                )

                return answer

            #
            # 3. LLMが選んだToolをAgent Runtimeが実行
            #
            self.logger.llm(
                f"{len(tool_calls)}個のToolを選択"
            )

            for number, call in enumerate(
                tool_calls,
                start=1,
            ):
                function = call.get(
                    "function",
                    {},
                )

                name = function.get(
                    "name"
                )

                arguments = function.get(
                    "arguments",
                    {},
                )

                # モデルによってJSON文字列になる場合への対応
                if isinstance(
                    arguments,
                    str,
                ):
                    try:
                        arguments = json.loads(
                            arguments
                        )
                    except json.JSONDecodeError:
                        arguments = {}

                self.logger.tool(
                    f"{number}/{len(tool_calls)} "
                    f"{name}("
                    f"{json.dumps(arguments, ensure_ascii=False)}"
                    f")"
                )

                function_to_run = (
                    self.tool_registry.get(name)
                )

                started = time.time()

                if function_to_run is None:
                    result = (
                        f"ERROR: Unknown tool: {name}"
                    )

                else:
                    try:
                        result = function_to_run(
                            **arguments
                        )

                    except TypeError as exc:
                        result = (
                            "ERROR: invalid arguments: "
                            f"{exc}"
                        )

                    except Exception as exc:
                        result = (
                            f"ERROR: "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )

                elapsed = (
                    time.time() - started
                )

                self.logger.result(
                    f"{name} completed "
                    f"({elapsed:.2f}s)"
                )

                self._print_tool_result(
                    str(result)
                )

                #
                # 4. Tool結果をLLMへ戻す
                #
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": str(result),
                    }
                )

            #
            # 次のターンでLLMが結果を見て再判断
            #
            self.logger.llm(
                "Tool結果をLLMへ返却 → 再判断"
            )

        raise RuntimeError(
            "Agent reached maximum turns: "
            f"{self.max_turns}"
        )

    def _print_tool_result(
        self,
        result: str,
        max_chars: int = 1500,
    ) -> None:
        if self.logger.quiet:
            return

        preview = result

        if len(preview) > max_chars:
            preview = (
                preview[:max_chars]
                + "\n...[truncated]"
            )

        for line in preview.splitlines():
            print(
                f"         {line}",
                flush=True,
            )

    def _print_metrics(
        self,
        response: dict[str, Any],
    ) -> None:
        if self.logger.quiet:
            return

        total_ns = response.get(
            "total_duration"
        )

        load_ns = response.get(
            "load_duration"
        )

        eval_count = response.get(
            "eval_count"
        )

        eval_ns = response.get(
            "eval_duration"
        )

        self.logger.section(
            "OLLAMA METRICS"
        )

        if total_ns:
            self.logger.log(
                f"total : "
                f"{total_ns / 1_000_000_000:.2f}s"
            )

        if load_ns:
            self.logger.log(
                f"load  : "
                f"{load_ns / 1_000_000_000:.2f}s"
            )

        if (
            eval_count
            and eval_ns
            and eval_ns > 0
        ):
            tokens_per_second = (
                eval_count
                / (
                    eval_ns
                    / 1_000_000_000
                )
            )

            self.logger.log(
                f"eval  : "
                f"{eval_count} tokens "
                f"({tokens_per_second:.2f} tok/s)"
            )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "VIGIL local LLM agent "
            "using Ollama tool calling"
        )
    )

    parser.add_argument(
        "prompt",
        nargs="?",
        help="Task for the agent",
    )

    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
    )

    parser.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
    )

    parser.add_argument(
        "--workspace",
        default=".",
    )

    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=(
            "Ollama HTTP timeout seconds "
            f"(default: {DEFAULT_TIMEOUT})"
        ),
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Hide Agent progress logs",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.prompt:
        print(
            "ERROR: prompt is required",
            file=sys.stderr,
        )

        return 2

    try:
        workspace = Workspace(
            args.workspace
        )

        logger = Logger(
            quiet=args.quiet
        )

        tools = Tools(
            workspace
        )

        ollama = OllamaClient(
            base_url=args.ollama_url,
            model=args.model,
            timeout=args.timeout,
            logger=logger,
        )

        agent = AgentRuntime(
            ollama=ollama,
            tools=tools,
            logger=logger,
            max_turns=args.max_turns,
        )

        agent.run(
            args.prompt
        )

        return 0

    except KeyboardInterrupt:
        print(
            "\nInterrupted.",
            file=sys.stderr,
        )

        return 130

    except Exception as exc:
        print(
            f"\nERROR: {exc}",
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())