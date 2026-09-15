import json
import threading

import pytest

import client
from client import parse_command
from protocol import HELP, NICK, QUIT, USERS


class FakeSocket:

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(json.loads(data.decode().rstrip("\n")))


@pytest.fixture
def sock():
    return FakeSocket()


@pytest.fixture
def stop():
    return threading.Event()


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [
        ("/help", (HELP, [])),
        ("/HELP", (HELP, [])),
        ("/nick alice", (NICK, ["alice"])),
        ("/nick   alice  ", (NICK, ["alice"])),
        ("/nick alice bob", (NICK, ["alice", "bob"])),
        ("/users", (USERS, [])),
    ],
)
def test_parse_command(saisie, attendu):
    assert parse_command(saisie) == attendu


def test_help_ne_part_pas_sur_le_réseau(sock, stop):
    assert client.run_command(sock, "/help", stop) is True
    assert sock.sent == []


def test_commande_inconnue_ne_part_pas_sur_le_réseau(sock, stop):
    assert client.run_command(sock, "/dance", stop) is True
    assert sock.sent == []


def test_users_envoie_une_commande(sock, stop):
    client.run_command(sock, "/users", stop)
    assert sock.sent == [{"type": "command", "payload": {"name": USERS, "args": []}}]


def test_nick_envoie_le_pseudo(sock, stop):
    client.run_command(sock, "/nick alice", stop)
    assert sock.sent == [{"type": "command", "payload": {"name": NICK, "args": ["alice"]}}]


@pytest.mark.parametrize("saisie", ["/nick", "/nick alice bob"])
def test_nick_de_mauvaise_arité_ne_part_pas(sock, stop, saisie):
    """Une commande incomplète est signalée en local, sans aller-retour réseau."""
    assert client.run_command(sock, saisie, stop) is True
    assert sock.sent == []


def test_quit_envoie_la_commande_et_arrête_la_boucle(sock, stop):
    assert client.run_command(sock, "/quit", stop) is False
    assert sock.sent == [{"type": "command", "payload": {"name": QUIT, "args": []}}]


def test_quit_annonce_la_fin_avant_denvoyer(sock, stop):
    """Sinon le thread de réception prend le départ voulu pour une panne."""
    client.run_command(sock, "/quit", stop)
    assert stop.is_set()


@pytest.mark.parametrize("saisie", ["/help", "/users", "/nick alice", "/dance"])
def test_les_autres_commandes_ne_terminent_pas_la_session(sock, stop, saisie):
    client.run_command(sock, saisie, stop)
    assert not stop.is_set()
