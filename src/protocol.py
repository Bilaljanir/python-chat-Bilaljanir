import codecs
import json
import socket
import time
from collections.abc import Callable, Iterator

MAX_MESSAGE_LEN = 4096
MAX_TEXT_LEN = 1024
RECV_SIZE = 1024

CHAT = "chat"
SYSTEM = "system"
COMMAND = "command"

ASK_USERNAME = "ask_username"
WELCOME = "welcome"
ERROR = "error"
JOIN = "join"
LEAVE = "leave"
NOTICE = "notice"
RENAME = "rename"
USER_LIST = "user_list"

HELP = "help"
NICK = "nick"
QUIT = "quit"
USERS = "users"

REQUIRED_FIELDS = {
    CHAT: ("text",),
    SYSTEM: ("event", "text"),
    COMMAND: ("name",),
}


class MessageTooLong(Exception):
    """Une ligne dépasse MAX_MESSAGE_LEN caractères."""

class InvalidMessage(Exception):
    """La ligne reçue n'est pas un message conforme au protocole."""

def chat_message(text: str, username: str | None = None) -> dict:
    payload = {"text": text}
    if username is not None:
        payload["username"] = username
    return {"type": CHAT, "payload": payload}


def system_message(event: str, text: str, **extra: object) -> dict:
    return {"type": SYSTEM, "payload": {"event": event, "text": text, **extra}}


def command_message(name: str, *args: str) -> dict:
    return {"type": COMMAND, "payload": {"name": name, "args": list(args)}}


def encode(message: dict) -> str:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def decode(line: str) -> dict:

    try:
        message = json.loads(line)
    except json.JSONDecodeError as e:
        raise InvalidMessage(f"JSON illisible : {e}") from e
    except RecursionError as e:
        raise InvalidMessage("JSON trop imbriqué") from e

    if not isinstance(message, dict):
        raise InvalidMessage("le message n'est pas un objet JSON")

    message_type = message.get("type")
    if message_type not in REQUIRED_FIELDS:
        raise InvalidMessage(f"type inconnu : {message_type!r}")

    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise InvalidMessage("« payload » absent ou n'est pas un objet")

    _check_payload(message_type, payload)
    return {"type": message_type, "payload": payload}


def _check_payload(message_type: str, payload: dict) -> None:
    for field in REQUIRED_FIELDS[message_type]:
        if not isinstance(payload.get(field), str):
            raise InvalidMessage(
                f"champ « {field} » absent ou non textuel dans un message {message_type}"
            )

    if message_type == CHAT:
        _check_optional_text(payload, "username")
    elif message_type == COMMAND:
        # Normalisé ici pour que le reste du code puisse écrire payload["args"].
        payload["args"] = _checked_args(payload.get("args", []))


def _check_optional_text(payload: dict, field: str) -> None:
    if field in payload and not isinstance(payload[field], str):
        raise InvalidMessage(f"champ « {field} » non textuel")


def _checked_args(args: object) -> list[str]:
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise InvalidMessage("« args » doit être une liste de chaînes")
    return args


def send_line(sock: socket.socket, text: str) -> None:
    sock.sendall(f"{text}\n".encode())


def send_message(sock: socket.socket, message: dict) -> None:
    send_line(sock, encode(message))

def iter_messages(
    reader: "LineReader",
    on_invalid: Callable[[str, InvalidMessage], None],
) -> Iterator[dict]:
    for line in reader.lines():
        if not line.strip():
            continue
        try:
            message = decode(line)
        except InvalidMessage as e:
            on_invalid(line, e)
            continue
        yield message


class LineReader:

    def __init__(self, sock: socket.socket, idle_timeout: float | None = None) -> None:
        self._sock = sock
        self._idle_timeout = idle_timeout
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._buffer = ""
        self._deadline = time.monotonic() + (idle_timeout or 0)
        self._lines: Iterator[str] | None = None
        self.timed_out = False

    def lines(self) -> Iterator[str]:
        if self._lines is None:
            self._lines = self._iter_lines()
        return self._lines

    def _iter_lines(self) -> Iterator[str]:
        while True:
            yield from self._drain_buffer()
            if not self._fill_buffer():
                return

    def _drain_buffer(self) -> Iterator[str]:
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._reject_if_too_long(line)
            yield line.rstrip("\r")
        self._reject_if_too_long(self._buffer)

    def _fill_buffer(self) -> bool:
        if not self._arm_timeout():
            return False
        try:
            data = self._sock.recv(RECV_SIZE)
        except TimeoutError:
            self.timed_out = True
            return False
        if not data:
            return False
        if self._idle_timeout is not None:
            self._deadline = time.monotonic() + self._idle_timeout
        self._buffer += self._decoder.decode(data)
        return True

    def _arm_timeout(self) -> bool:
        if self._idle_timeout is None:
            return True
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            self.timed_out = True
            return False
        self._sock.settimeout(remaining)
        return True

    @staticmethod
    def _reject_if_too_long(line: str) -> None:
        if len(line) > MAX_MESSAGE_LEN:
            raise MessageTooLong(f"ligne de {len(line)} caractères")
