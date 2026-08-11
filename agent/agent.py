#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from tools import TOOLS, TOOL_SCHEMAS

DEFAULT_SYSTEM = """You are VIGIL, an infrastructure investigation agent running in a restricted AWS environment.
Use tools to verify facts instead of guessing. You may make multiple tool calls.
For network isolation questions, distinguish Terraform intent from runtime connectivity checks.
A failed Internet connectivity test is evidence of the observed runtime result, not mathematical proof of all possible paths.
When enough evidence is collected, answer concisely in Japanese with the evidence you actually observed.
"""


def ollama_chat(base_url: str, model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach Ollama at {base_url}: {exc}") from exc


def run_agent(prompt: str, model: str, base_url: str, max_turns: int, verbose: bool) -> str:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": DEFAULT_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    for turn in range(1, max_turns + 1):
        response = ollama_chat(base_url, model, messages)
        message = response.get("message", {})
        if not isinstance(message, dict):
            raise RuntimeError(f"Unexpected Ollama response: {response}")
        messages.append(message)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return str(message.get("content", ""))

        for call in tool_calls:
            function = call.get("function", {})
            name = function.get("name", "")
            args = function.get("arguments", {}) or {}
            if not isinstance(args, dict):
                result = f"Invalid tool arguments for {name}: {args!r}"
            elif name not in TOOLS:
                result = f"Unknown tool: {name}"
            else:
                if verbose:
                    print(f"\n[TOOL {turn}] {name}({json.dumps(args, ensure_ascii=False)})", flush=True)
                try:
                    result = TOOLS[name](**args)
                except Exception as exc:  # Tool errors should go back to the model.
                    result = f"Tool error: {type(exc).__name__}: {exc}"
                if verbose:
                    print(str(result)[:4000], flush=True)

            messages.append(
                {
                    "role": "tool",
                    "tool_name": name,
                    "content": str(result),
                }
            )

    raise RuntimeError(f"Agent exceeded max_turns={max_turns} without a final answer")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="VIGIL local LLM agent using Ollama tool calling")
    p.add_argument("prompt", nargs="?", help="Task for the agent")
    p.add_argument("--model", default=os.environ.get("VIGIL_MODEL", "vigil-agent"))
    p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    p.add_argument("--workspace", default=os.environ.get("VIGIL_WORKSPACE", os.getcwd()))
    p.add_argument("--max-turns", type=int, default=20)
    p.add_argument("--quiet", action="store_true", help="Hide tool execution logs")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not args.prompt:
        build_parser().print_help()
        return 0
    os.environ["VIGIL_WORKSPACE"] = args.workspace
    try:
        answer = run_agent(args.prompt, args.model, args.ollama_url, args.max_turns, not args.quiet)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("\n=== FINAL ANSWER ===\n")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
