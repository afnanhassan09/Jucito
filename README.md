# PureWaterBot (Phase 1)

This repo contains the **Phase 1 skeleton**: data structures, an in-memory `SessionManager`, a dummy menu, and a mock terminal chat loop (no WhatsApp integration yet).

## What you get

- `data/menu.json`: dummy products (name, description, tags, price)
- `purewaterbot/`: models + session manager + simple bot engine
- `bot_server.py`: a tiny stdin/stdout “bot server” that preserves sessions in memory
- `cli.js`: a terminal chat loop that talks to the Python server

## Run the CLI

From the repo root:

```bashl
ollama serve
ollama pull qwen2.5:7b
node cli.js
```

Notes:
- Requires `python3` on PATH and Node.js installed.
- Type `help` in the CLI for available commands.

