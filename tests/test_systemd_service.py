from pathlib import Path


def test_gateway_waits_for_network_and_always_restarts() -> None:
    unit = Path("deploy/codex-session-gateway.service").read_text(encoding="utf-8")

    assert "Wants=network-online.target" in unit
    assert "After=network-online.target" in unit
    assert "Restart=always" in unit
    assert "RestartSec=5" in unit
