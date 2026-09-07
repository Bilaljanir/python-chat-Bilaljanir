import codecs
import socket
import time
from collections.abc import Iterator

MAX_MESSAGE_LEN = 4096
RECV_SIZE = 1024

ASK = "ASK"
ERR = "ERR"
OK = "OK"


class MessageTooLong(Exception):
    """Une ligne dépasse MAX_MESSAGE_LEN caractères."""

def send_line(sock: socket.socket, text: str) -> None:
    sock.sendall(f"{text}\n".encode())


def split_tag(line: str) -> tuple[str, str]:
    tag, _, rest = line.partition(" ")
    return tag, rest

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
