# Auto Resume ID ("latest session") Design

## Goal
When a Codex session runs for a long time, users may need to `/new` a fresh conversation in Codex CLI. That creates a new session id and a new JSONL file under `~/.codex/sessions/**`.

This project should support automatically following the latest *main* Codex session for a given bot/workdir, without the user manually updating `resume_id` in config.

## Config
For each bot:
- `resume_id = "<fixed-id>"` keeps the existing behavior.
- `resume_id = "auto"` enables auto-follow.

Selection rule (this implementation):
- Match sessions where `session_meta.payload.cwd == codex_workdir` (exact match).
- Ignore subagent sessions.

## How We Detect Latest Session
Scan `~/.codex/sessions/**.jsonl` and read only the first line of each file:
- Require `type == "session_meta"`.
- Require `payload.cwd == codex_workdir`.
- Ignore if `payload.source` indicates subagent:
  - `payload.source` is a dict that contains `"subagent"`.
- Pick the session with the max `payload.timestamp` (ISO8601 parsed to epoch seconds).

We keep a short in-process cache (0.5s) to avoid re-scanning on every call.

## Runtime Integration
New API:
- `CodexRunner.resolve_resume_id(resume_id: str | None) -> str | None`
  - For `"auto"`, resolve to latest session id for `codex_workdir`.

Used in:
- `CodexRunner.run()` and `CodexRunner._run_with_pty()`:
  - Resolve before building args.
  - Ensure JSONL tailer uses the resolved id (so stream events keep working).
- `Orchestrator.get_resume_id()`:
  - Resolve before returning (used by JSONL sync and status).
- `Orchestrator.last_result()` and `Orchestrator.status()`:
  - Use the resolved resume id (so `/lastresult` and `/status` work in `auto` mode).

## Behavior Notes
- Switching is allowed even while Telegram-triggered tasks are running.
- Subagent sessions are never selected as the active resume id.
- If no session exists yet, `"auto"` resolves to `None`:
  - Codex execution falls back to non-resume mode.
  - JSONL polling has nothing to read until a session is created.

## Tests
Added tests cover:
- Building args resolves `"auto"` to the latest main session and ignores subagents.
- `run()` uses the resolved resume id for JSONL tailer (stream emits messages).
- `Orchestrator.get_resume_id()`, `/lastresult`, `/status` work with `"auto"`.

