import asyncio
import json
import time
import pytest

from src.codex_runner import CodexRunner
from src.config import Config


def test_codex_runner_build_args_with_resume():
    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=["--model", "x"],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id="resume-abc",
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.1,
        stream_include_stderr=False,
        progress_tick_interval=1.0,
        run_timeout_seconds=5.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    runner = CodexRunner(config)
    args, use_exec = runner._build_args_for_prompt("hello", None)
    assert use_exec is True
    assert args == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--model",
        "x",
        "resume",
        "resume-abc",
        "-",
    ]


def test_codex_runner_build_args_override_resume():
    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id="resume-abc",
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.1,
        stream_include_stderr=False,
        progress_tick_interval=1.0,
        run_timeout_seconds=5.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    runner = CodexRunner(config)
    args, use_exec = runner._build_args_for_prompt("hello", "resume-override")
    assert use_exec is True
    assert args == ["codex", "exec", "--skip-git-repo-check", "resume", "resume-override", "-"]


def test_codex_runner_build_args_with_output_last_message():
    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=["--model", "x"],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id="resume-abc",
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.1,
        stream_include_stderr=False,
        progress_tick_interval=1.0,
        run_timeout_seconds=5.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    runner = CodexRunner(config)
    args, use_exec = runner._build_args_for_prompt(
        "hello", None, "/tmp/last-message.txt"
    )
    assert use_exec is True
    assert "--output-last-message" in args
    assert "/tmp/last-message.txt" in args


def test_codex_runner_build_env_sets_dbus(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    bus_path = runtime_dir / "bus"
    bus_path.write_text("")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode="3",
        codex_cli_skip_git_check=True,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.1,
        stream_include_stderr=False,
        progress_tick_interval=1.0,
        run_timeout_seconds=5.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=900.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    runner = CodexRunner(config)
    env = runner._build_env()

    assert env["XDG_RUNTIME_DIR"] == str(runtime_dir)
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={bus_path}"


def test_codex_runner_detects_context_compacted():
    assert CodexRunner._is_context_compacted("Context compacted")
    assert CodexRunner._is_context_compacted("context compacted")
    assert not CodexRunner._is_context_compacted("context compressed")


@pytest.mark.asyncio
async def test_codex_runner_uses_last_message_and_finishes(tmp_path):
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import argparse\n"
        "import time\n"
        "\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--output-last-message')\n"
        "args, _ = parser.parse_known_args()\n"
        "if args.output_last_message:\n"
        "    with open(args.output_last_message, 'w', encoding='utf-8') as handle:\n"
        "        handle.write('final result')\n"
        "print('Context compacted', flush=True)\n"
        "time.sleep(10)\n"
    )
    script.chmod(0o755)

    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd=str(script),
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode=None,
        codex_cli_skip_git_check=False,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.05,
        run_timeout_seconds=2.0,
        context_compaction_idle_timeout_seconds=0.1,
        no_output_idle_timeout_seconds=2.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    runner = CodexRunner(config)

    outputs = []

    async def on_output(text: str, is_error: bool) -> None:
        outputs.append(text)

    async def on_status(status: str) -> None:
        return None

    return_code = await runner.run("hello", on_output, on_status)

    assert return_code == 0
    assert any("final result" in text for text in outputs)


@pytest.mark.asyncio
async def test_codex_runner_final_result_idle_terminates(tmp_path):
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import argparse\n"
        "import time\n"
        "\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--output-last-message')\n"
        "args, _ = parser.parse_known_args()\n"
        "if args.output_last_message:\n"
        "    with open(args.output_last_message, 'w', encoding='utf-8') as handle:\n"
        "        handle.write('final result')\n"
        "time.sleep(10)\n"
    )
    script.chmod(0o755)

    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd=str(script),
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode=None,
        codex_cli_skip_git_check=False,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.05,
        run_timeout_seconds=2.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=5.0,
        final_result_idle_timeout_seconds=0.1,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    runner = CodexRunner(config)

    outputs = []
    statuses = []

    async def on_output(text: str, is_error: bool) -> None:
        outputs.append(text)

    async def on_status(status: str) -> None:
        statuses.append(status)

    return_code = await runner.run("hello", on_output, on_status)

    assert return_code == 0
    assert any("final result" in text for text in outputs)
    assert any("自动结束" in text for text in outputs)
    assert "timeout" not in statuses


@pytest.mark.asyncio
async def test_codex_runner_dedupes_duplicate_outputs(tmp_path):
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "print('dup', flush=True)\n"
        "print('dup', flush=True)\n"
    )
    script.chmod(0o755)

    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd=str(script),
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode=None,
        codex_cli_skip_git_check=False,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.05,
        run_timeout_seconds=2.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=2.0,
        final_result_idle_timeout_seconds=0.1,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    runner = CodexRunner(config)

    outputs = []

    async def on_output(text: str, is_error: bool) -> None:
        outputs.append(text)

    async def on_status(status: str) -> None:
        return None

    return_code = await runner.run("hello", on_output, on_status)

    assert return_code == 0
    assert outputs.count("dup") == 1


@pytest.mark.asyncio
async def test_codex_runner_idle_no_output_times_out(tmp_path):
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import time\n"
        "time.sleep(10)\n"
    )
    script.chmod(0o755)

    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd=str(script),
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode=None,
        codex_cli_skip_git_check=False,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.05,
        run_timeout_seconds=2.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=0.1,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=False,
        jsonl_reasoning_throttle_seconds=10.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    runner = CodexRunner(config)
    statuses = []

    async def on_output(text: str, is_error: bool) -> None:
        return None

    async def on_status(status: str) -> None:
        statuses.append(status)

    return_code = await runner.run("hello", on_output, on_status)

    assert return_code == 0
    assert "timeout" in statuses


def test_codex_runner_extracts_last_assistant_message(tmp_path):
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        "\n".join(
            [
                '{"timestamp":"2026-01-01T00:00:00Z","type":"event_msg","payload":{"type":"agent_message","message":"hello"}}',
                '{"timestamp":"2026-01-01T00:00:01Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"first"}]}}',
                '{"timestamp":"2026-01-01T00:00:02Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"second"}]}}',
            ]
        )
        + "\n"
    )

    last = CodexRunner._extract_last_assistant_message(str(session_file))
    assert last == "second"
    message, timestamp = CodexRunner._extract_last_assistant_message_with_ts(
        str(session_file)
    )
    assert message == "second"
    assert timestamp is not None


def test_codex_runner_parses_event_messages():
    data = json.loads(
        '{"type":"event_msg","payload":{"type":"agent_reasoning","text":"thinking"}}'
    )
    text, is_reasoning = CodexRunner._event_msg_text(data)
    assert text == "thinking"
    assert is_reasoning is True

    data = json.loads(
        '{"type":"event_msg","payload":{"type":"agent_message","message":"hello"}}'
    )
    text, is_reasoning = CodexRunner._event_msg_text(data)
    assert text == "hello"
    assert is_reasoning is False


def test_codex_runner_summarizes_reasoning_safely():
    raw = "Preparing final response with unique-token-xyz and test steps."
    summary = CodexRunner._summarize_reasoning(raw)
    assert "内部推理摘要" in summary
    assert "unique-token-xyz" not in summary


def test_codex_runner_normalizes_dedupe_text():
    text = "line 1  \r\nline 2\t\r\n\r\n"
    normalized = CodexRunner._normalize_text_for_dedupe(text)
    assert normalized == "line 1\nline 2"


@pytest.mark.asyncio
async def test_codex_runner_tails_jsonl_and_relocates(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions"
    sessions_dir.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    resume_id = "resume-rotate"
    file_one = sessions_dir / f"rollout-1-{resume_id}.jsonl"
    file_one.write_text("")

    config = Config(
        telegram_bot_token="token",
        telegram_allowed_user_ids={1},
        codex_cli_cmd="codex",
        codex_cli_args=[],
        codex_cli_input_mode="stdin",
        codex_cli_resume_id=None,
        codex_cli_approvals_mode=None,
        codex_cli_skip_git_check=False,
        codex_cli_use_pty=False,
        codex_workdir=".",
        stream_flush_interval=0.01,
        stream_include_stderr=False,
        progress_tick_interval=0.05,
        run_timeout_seconds=2.0,
        context_compaction_idle_timeout_seconds=60.0,
        no_output_idle_timeout_seconds=2.0,
        final_result_idle_timeout_seconds=30.0,
        jsonl_sync_interval_seconds=0.0,
        jsonl_stream_events=True,
        jsonl_reasoning_throttle_seconds=0.0,
        jsonl_reasoning_mode="hidden",
        message_chunk_limit=1000,
    )
    runner = CodexRunner(config)

    outputs: list[str] = []
    finished = asyncio.Event()

    async def emit(text: str) -> None:
        outputs.append(text)

    task = asyncio.create_task(
        runner._tail_jsonl_events(resume_id, finished, emit)
    )

    await asyncio.sleep(0.2)
    with open(file_one, "a", encoding="utf-8") as handle:
        handle.write(
            '{"type":"event_msg","payload":{"type":"agent_message","message":"one"}}\n'
        )

    async def wait_for_output(expected: str) -> None:
        start = time.monotonic()
        while expected not in outputs:
            if time.monotonic() - start > 2.0:
                raise AssertionError(f"missing output: {expected}")
            await asyncio.sleep(0.05)

    await wait_for_output("one")

    file_one.unlink()
    file_two = sessions_dir / f"rollout-2-{resume_id}.jsonl"
    file_two.write_text("")
    await asyncio.sleep(0.6)
    with open(file_two, "a", encoding="utf-8") as handle:
        handle.write(
            '{"type":"event_msg","payload":{"type":"agent_message","message":"two"}}\n'
        )

    await wait_for_output("two")

    finished.set()
    await asyncio.wait_for(task, timeout=2.0)
