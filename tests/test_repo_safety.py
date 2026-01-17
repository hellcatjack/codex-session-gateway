from __future__ import annotations

from pathlib import Path


def test_gitignore_blocks_local_config() -> None:
    text = Path(".gitignore").read_text(encoding="utf-8")
    assert "config.toml" in text
    assert ".env.old" in text


def test_examples_do_not_contain_leaked_ids() -> None:
    leaked_ids = {"123456789", "11223344"}
    paths = [Path("README.md"), Path("config.toml.example")]
    for path in paths:
        content = path.read_text(encoding="utf-8")
        for leaked in leaked_ids:
            assert leaked not in content
