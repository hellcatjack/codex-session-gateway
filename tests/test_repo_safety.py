from __future__ import annotations

import re
from pathlib import Path


def test_gitignore_blocks_local_config() -> None:
    text = Path(".gitignore").read_text(encoding="utf-8")
    assert "config.toml" in text
    assert ".env.old" in text


def test_examples_use_placeholder_ids() -> None:
    allowed_ids = {"123456789", "987654321"}
    pattern = re.compile(r"\\b\\d{9,}\\b")
    paths = [Path("README.md"), Path("config.toml.example")]
    for path in paths:
        content = path.read_text(encoding="utf-8")
        for match in pattern.findall(content):
            assert match in allowed_ids
