from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CommandType(str, Enum):
    STOP = "stop"
    STATUS = "status"
    RETRY = "retry"
    NEW = "new"
    HELP = "help"
    SESSION = "session"
    LASTRESULT = "lastresult"


@dataclass
class ParsedCommand:
    type: CommandType
    payload: Optional[str] = None


def parse_command(text: str) -> Optional[ParsedCommand]:
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].lower()
    payload = parts[1].strip() if len(parts) > 1 else None
    if cmd == "stop":
        return ParsedCommand(CommandType.STOP)
    if cmd == "status":
        return ParsedCommand(CommandType.STATUS)
    if cmd == "retry":
        return ParsedCommand(CommandType.RETRY)
    if cmd == "new":
        return ParsedCommand(CommandType.NEW, payload)
    if cmd == "session":
        return ParsedCommand(CommandType.SESSION, payload)
    if cmd == "help":
        return ParsedCommand(CommandType.HELP)
    if cmd == "lastresult":
        return ParsedCommand(CommandType.LASTRESULT)
    return None
