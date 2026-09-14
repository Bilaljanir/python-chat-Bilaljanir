"""Un vrai serveur sur un port éphémère, et des clients qui parlent le protocole.

Les tests d'intégration ne simulent rien : ils ouvrent des sockets vers le
serveur du dépôt, exactement comme le ferait `nc`. C'est la seule façon de
couvrir un protocole dont l'état vit dans un registre partagé entre threads.
"""

import json
import socket
import threading

import pytest
from rich.console import Console

import server as server_module
import ui
from server import serve

RECV_TIMEOUT = 3


@pytest.fixture(autouse=True)
def quiet_ui(monkeypatch):
    """Le client écrit dans le terminal : pas dans la sortie de pytest."""
    monkeypatch.setattr(ui, "console", Console(quiet=True))


class FakeClient:
    """Un pair minimal : envoie des lignes JSON, en lit une à la fois."""

    def __init__(self, port: int) -> None:
        self._sock = socket.create_connection(("localhost", port))
        self._sock.settimeout(RECV_TIMEOUT)
        self._buffer = ""

    def send_raw(self, line: str) -> None:
        self._sock.sendall(f"{line}\n".encode())

    def command(self, name: str, *args: str) -> None:
        self.send_raw(
            json.dumps({"type": "command", "payload": {"name": name, "args": list(args)}})
        )

    def chat(self, text: str) -> None:
        self.send_raw(json.dumps({"type": "chat", "payload": {"text": text}}))

    def receive(self) -> dict | None:
        """Le prochain message, ou None si le serveur a fermé la connexion."""
        while "\n" not in self._buffer:
            try:
                data = self._sock.recv(4096)
            except TimeoutError:
                raise AssertionError("aucun message reçu avant le délai") from None
            if not data:
                return None
            self._buffer += data.decode()
        line, self._buffer = self._buffer.split("\n", 1)
        return json.loads(line)

    def drain(self, timeout: float = 0.3) -> list[dict]:
        """Tout ce qui est déjà arrivé, sans attendre la suite."""
        received = []
        self._sock.settimeout(timeout)
        try:
            while True:
                message = self.receive()
                if message is None:
                    break
                received.append(message)
        except (TimeoutError, AssertionError):
            pass
        self._sock.settimeout(RECV_TIMEOUT)
        return received

    def login(self, username: str) -> dict:
        """Poignée de main complète : renvoie le message `welcome`."""
        self.receive()  # ask_username
        self.command("nick", username)
        return self.receive()

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def chat_server():
    """Démarre `serve()` sur un port libre, et rend le registre vierge après coup."""
    # Les logs du serveur n'ont rien à faire dans la sortie de pytest.
    server_module.console = Console(quiet=True)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("localhost", 0))
    listener.listen()
    port = listener.getsockname()[1]

    threading.Thread(target=serve, args=(listener, 10, 30), daemon=True).start()
    try:
        yield port
    finally:
        listener.close()
        server_module.clients.clear()


@pytest.fixture
def connect(chat_server):
    """Ouvre des clients et les referme tous à la fin du test."""
    opened: list[FakeClient] = []

    def _connect(username: str | None = None) -> FakeClient:
        client = FakeClient(chat_server)
        opened.append(client)
        if username is not None:
            client.login(username)
        return client

    yield _connect
    for client in opened:
        client.close()
