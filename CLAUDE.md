# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

WithYuTogether is a Discord bot (discord.py, slash commands) whose user-facing text and comments are in Traditional Chinese. Cogs cover tarot, horoscope, food picker, reminders, MapleStory (Nexon API), and Valorant (HenrikDev API).

## Running

```
pip install -r requirements.txt
python main.py
```

Run from the repo root: all paths (`config.json`, `data/...`, `./cogs`) are relative to the working directory. There is no test suite, linter, or build step.

`config.json` (gitignored) holds `token`, `test_token`, `nexon_api_key`, `valorant_api_key`. `main.py` only reads `token`; the API keys are read directly from `config.json` by `cogs/maple_cog.py` and `utils/valorant_api.py`.

## Architecture

- `main.py` auto-loads every `cogs/*.py` as an extension (each must define `async def setup(bot)`), then calls `bot.tree.sync()` globally on every startup. The default `help` command is removed in favor of `help_cog`. A cog that fails to load is logged and skipped, not fatal.
- **Persistence is mid-migration from JSON files to SQLite.**
  - `utils/db_manager.py` — `DatabaseManager` is a singleton over `data/with_you_together.db`; `init_db()` creates all tables (`CREATE TABLE IF NOT EXISTS`). Currently only Valorant uses it. Each call opens its own short-lived `sqlite3` connection with `row_factory = sqlite3.Row`.
  - Reminders (`data/reminders.json`) and tarot history (`data/tarot_users.json`) still use JSON files, even though the DB already has `reminders`/`users` tables meant to replace them.
  - Schema changes to existing tables must be applied as in-place migrations (see `ValorantCog.__lazy_init_db`, which uses `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`), since `CREATE TABLE IF NOT EXISTS` won't alter an existing DB.
- `ValorantCog` creates the `DatabaseManager` lazily (`__lazy_init_db`) on first command and caches MMR/match data in `valorant_accounts`.

## Gitignored runtime data

`config.json`, `*.db`, `data/*_users.json`, `data/reminder*`, `img/*`. Only `data/tarot_cards.json` under `data/` is tracked.
