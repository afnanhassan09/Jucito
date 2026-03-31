#!/usr/bin/env python3
"""Quick check that OPENAI_API_KEY works with the gpt-4o-mini chat model."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _load_dotenv_file(path: Path) -> None:
    """
    Load KEY=VALUE pairs into os.environ (no python-dotenv required).
    - Reads UTF-8 with BOM stripped (Excel/some editors add a BOM).
    - Fills missing or empty vars only (same idea as load_dotenv override=False,
      but also replaces empty OPENAI_API_KEY so a blank export does not block .env).
    """
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        current = os.environ.get(key)
        if current is None or (isinstance(current, str) and not current.strip()):
            os.environ[key] = value


def _bootstrap_env() -> None:
    root = Path(__file__).resolve().parent
    _load_dotenv_file(root / ".env")
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
    except ImportError:
        pass

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"


def main() -> int:
    _bootstrap_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("Missing OPENAI_API_KEY. Set it in your environment or .env file.", file=sys.stderr)
        return 1

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": 'Reply with exactly the word "pong" and nothing else.'}
        ],
        "max_tokens": 16,
        "temperature": 0,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(err_body)
            detail = err.get("error", {}).get("message", err_body)
        except json.JSONDecodeError:
            detail = err_body or str(e)
        print(f"API error ({e.code}): {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Request failed: {e.reason}", file=sys.stderr)
        return 1

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON from API: {e}", file=sys.stderr)
        return 1

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = (msg.get("content") or "").strip()

    print(f"Model: {MODEL}")
    print(f"Reply: {text}")
    print("OK — API key is valid for chat completions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
