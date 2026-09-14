"""Le pseudo courant du client, et l'affichage de nos propres messages."""

import json
import threading

import pytest

import client
import ui
from protocol import JOIN, RENAME


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(json.loads(data.decode().rstrip("\n")))


@pytest.fixture
def lignes(monkeypatch):
    """Capte ce que le client affiche au lieu de l'écrire dans le terminal."""
    captées = []
    monkeypatch.setattr(ui, "emit", captées.append)
    return captées


def saisir(monkeypatch, *entrées: str) -> None:
    """Rejoue des saisies clavier, puis un Ctrl-D."""
    restantes = iter(entrées)

    def read_line(prompt: str | None = None) -> str:
        try:
            return next(restantes)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(ui, "read_line", read_line)


def test_le_pseudo_est_partagé_entre_les_threads():
    """Le thread réseau écrit le pseudo, le thread clavier le lit."""
    session = client.Session("bob")
    thread = threading.Thread(target=session.rename, args=("bobby",))
    thread.start()
    thread.join()
    assert session.username == "bobby"


def test_mon_renommage_met_à_jour_le_pseudo():
    session = client.Session("bob")
    client.track_rename(
        {"event": RENAME, "username": "bob", "new_username": "bobby"}, session
    )
    assert session.username == "bobby"


def test_le_renommage_dun_autre_ne_change_rien():
    """Les pseudos sont uniques : seul l'ancien nom qui est le nôtre nous concerne."""
    session = client.Session("bob")
    client.track_rename(
        {"event": RENAME, "username": "alice", "new_username": "carol"}, session
    )
    assert session.username == "bob"


def test_un_autre_évènement_ne_change_rien():
    session = client.Session("bob")
    client.track_rename({"event": JOIN, "username": "bob"}, session)
    assert session.username == "bob"


def test_mon_message_est_affiché_localement(monkeypatch, lignes):
    """Le serveur ne nous renvoie pas nos messages : le client les affiche lui-même."""
    sock = FakeSocket()
    saisir(monkeypatch, "bonjour")

    client.send_user_input(sock, threading.Event(), client.Session("alice"))

    assert sock.sent == [{"type": "chat", "payload": {"text": "bonjour"}}]
    mien = [line for line in lignes if "bonjour" in line.plain]
    assert len(mien) == 1
    assert mien[0].justify == "right"
    assert "alice" in mien[0].plain


def test_une_commande_naffiche_pas_de_message_à_moi(monkeypatch, lignes):
    sock = FakeSocket()
    saisir(monkeypatch, "/users")

    client.send_user_input(sock, threading.Event(), client.Session("alice"))

    assert [line for line in lignes if line.justify == "right"] == []


def test_un_message_trop_long_nest_ni_envoyé_ni_affiché(monkeypatch, lignes):
    sock = FakeSocket()
    saisir(monkeypatch, "x" * 2000)

    client.send_user_input(sock, threading.Event(), client.Session("alice"))

    assert sock.sent == []
    assert [line for line in lignes if line.justify == "right"] == []


def test_mes_messages_suivent_mon_nouveau_pseudo(monkeypatch, lignes):
    """Après un /nick confirmé, nos messages sont signés du nouveau nom."""
    sock = FakeSocket()
    session = client.Session("bob")
    saisies = iter(["avant", "après"])

    def read_line(prompt: str | None = None) -> str:
        try:
            texte = next(saisies)
        except StopIteration:
            raise EOFError from None
        if texte == "après":
            session.rename("carol")  # le serveur vient de confirmer le /nick
        return texte

    monkeypatch.setattr(ui, "read_line", read_line)
    client.send_user_input(sock, threading.Event(), session)

    signés = [line.plain for line in lignes if line.justify == "right"]
    assert len(signés) == 2
    assert "bob" in signés[0]
    assert "carol" in signés[1]


def fermeture(monkeypatch, stop, awaiting: bool) -> list[str]:
    """Rejoue une connexion fermée par le serveur, et rend les signaux envoyés."""
    signaux = []
    monkeypatch.setattr(ui, "awaiting_input", lambda: awaiting)
    monkeypatch.setattr(client, "interrupt_input", lambda: signaux.append("SIGINT"))
    client.receive_messages(iter([]), stop, client.Session("alice"))
    return signaux


def test_une_fermeture_réveille_un_input_en_cours(monkeypatch, lignes):
    stop = threading.Event()
    assert fermeture(monkeypatch, stop, awaiting=True) == ["SIGINT"]
    assert stop.is_set()


def test_une_fermeture_ne_signale_rien_si_personne_nattend(monkeypatch, lignes):
    """La boucle clavier verra stop au prochain tour : le signal ferait du bruit."""
    assert fermeture(monkeypatch, threading.Event(), awaiting=False) == []


def test_un_départ_volontaire_ne_déclenche_aucune_alerte(monkeypatch, lignes):
    """Sur /quit, stop est déjà posé : la fermeture est attendue."""
    stop = threading.Event()
    stop.set()
    assert fermeture(monkeypatch, stop, awaiting=True) == []
    assert lignes == []
