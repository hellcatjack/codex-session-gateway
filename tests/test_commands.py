from src.commands import CommandType, parse_command


def test_parse_command_basic():
    cmd = parse_command("/stop")
    assert cmd is not None
    assert cmd.type == CommandType.STOP


def test_parse_command_new_payload():
    cmd = parse_command("/new hello")
    assert cmd is not None
    assert cmd.type == CommandType.NEW
    assert cmd.payload == "hello"


def test_parse_command_none():
    assert parse_command("hello") is None


def test_parse_command_session():
    cmd = parse_command("/session 123")
    assert cmd is not None
    assert cmd.type == CommandType.SESSION
    assert cmd.payload == "123"
