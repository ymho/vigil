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
DEFAULT_MAX_TURNS = 20


SYSTEM_PROMPT = """
あなたはVIGIL環境で動作する調査エージェントです。

与えられた任務を達成するために、
利用可能なToolを自分で選択して使用してください。

Toolの実行結果を確認し、
必要であれば追加の調査を行ってください。

不確かな点を推測で補わず、
利用可能なToolで確認できることは確認してから
結論を出してください。

調査対象に関係する可能性があるファイルや設定が複数存在する場合、
一部だけを確認して結論を出さず、
合理的に関連し得るものを洗い出して確認してください。

検索結果が0件だった場合も、
その結果だけで不存在を断定せず、
別の表現・別のファイル・関連設定から確認できないか検討してください。

一つの根拠だけで結論を出せる場合を除き、
構成・実行状態・実際の観測結果など、
独立した複数の根拠を可能な範囲で確認してください。

調査を終了する前に、
「まだ確認可能なのに未確認の重要事項がないか」を一度見直してください。

確認できた事実については曖昧な表現を避け、
根拠を示して明確に回答してください。

確認済みの事実と未確認事項は区別してください。
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

        review_done = False

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

                # =====================================================
                # Review Turn
                # =====================================================

                if not review_done:
                    review_done = True

                    self.logger.llm(
                        "最終回答の前に調査内容をレビュー"
                    )

                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "調査を終了しようとしています。\n\n"
                                "ここまでのTool実行結果と確認済みの事実を見直し、"
                                "結論に影響し得る重要な未確認事項が残っていないか"
                                "点検してください。\n\n"
                                "まだ利用可能なToolで合理的に確認できる重要事項が"
                                "残っている場合は、最終回答を出さずに追加調査を"
                                "行ってください。\n\n"
                                "十分に確認できている場合のみ、"
                                "根拠を整理して最終回答してください。\n\n"
                                "新たな推測で穴を埋めず、"
                                "これまでの観測結果と追加確認できる事実に基づいて"
                                "判断してください。"
                            ),
                        }
                    )

                    self.logger.llm(
                        "Review Turnへ移行"
                    )

                    continue

                # =====================================================
                # Final Answer
                # =====================================================

                answer = (
                    message.get(
                        "content",
                        "",
                    ).strip()
                )

                self.logger.llm(
                    "レビュー完了 → 任務完了"
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