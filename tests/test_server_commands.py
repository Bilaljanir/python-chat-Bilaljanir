"""Les commandes vues du réseau : deux clients, un serveur, des sockets réelles."""

import socket

import server
from protocol import CHAT, ERROR, LEAVE, RENAME, SYSTEM, USER_LIST


def events(messages: list[dict]) -> list[str]:
    return [m["payload"]["event"] for m in messages if m["type"] == SYSTEM]


def test_users_liste_les_connectés(connect):
    connect("alice")
    bob = connect("bob")
    bob.drain()

    bob.command("users")
    payload = bob.receive()["payload"]

    assert payload["event"] == USER_LIST
    assert payload["users"] == ["alice", "bob"]
    assert "alice, bob" in payload["text"]


def test_users_est_trié_sans_tenir_compte_de_la_casse(connect):
    connect("Charlie")
    connect("alice")
    bob = connect("bob")
    bob.drain()

    bob.command("users")
    assert bob.receive()["payload"]["users"] == ["alice", "bob", "Charlie"]


def test_nick_renomme_et_prévient_les_autres(connect):
    alice = connect("alice")
    bob = connect("bob")
    alice.drain()

    bob.command("nick", "bobby")

    confirmation = bob.receive()["payload"]
    assert confirmation["event"] == RENAME
    assert confirmation["username"] == "bob"
    assert confirmation["new_username"] == "bobby"

    notification = alice.receive()["payload"]
    assert notification["event"] == RENAME
    assert "bob est désormais bobby" in notification["text"]


def test_nick_refuse_un_pseudo_déjà_pris(connect):
    connect("alice")
    bob = connect("bob")
    bob.drain()

    bob.command("nick", "ALICE")

    assert bob.receive()["payload"]["event"] == ERROR
    bob.command("users")
    assert bob.receive()["payload"]["users"] == ["alice", "bob"]


def test_nick_accepte_de_changer_la_casse_de_son_propre_pseudo(connect):
    bob = connect("bob")
    bob.drain()

    bob.command("nick", "BOB")

    assert bob.receive()["payload"]["event"] == RENAME
    bob.command("users")
    assert bob.receive()["payload"]["users"] == ["BOB"]


def test_nick_refuse_un_pseudo_invalide(connect):
    bob = connect("bob")
    bob.drain()

    bob.command("nick", "bob l'éponge !")

    assert bob.receive()["payload"]["event"] == ERROR


def test_nick_sans_argument_ne_casse_pas_la_session(connect):
    """Un pair qui n'est pas notre client peut envoyer n'importe quoi."""
    bob = connect("bob")
    bob.drain()

    bob.send_raw('{"type":"command","payload":{"name":"nick"}}')

    assert bob.receive()["payload"]["event"] == ERROR
    bob.command("users")
    assert bob.receive()["payload"]["event"] == USER_LIST


def test_le_chat_est_attribué_au_nouveau_pseudo(connect):
    alice = connect("alice")
    bob = connect("bob")
    alice.drain()

    bob.command("nick", "carol")
    bob.receive()
    alice.drain()
    bob.chat("salut")

    message = alice.receive()
    assert message["type"] == CHAT
    assert message["payload"] == {"text": "salut", "username": "carol"}


def test_le_départ_annonce_le_nouveau_pseudo(connect):
    alice = connect("alice")
    bob = connect("bob")
    alice.drain()

    bob.command("nick", "carol")
    bob.receive()
    alice.drain()
    bob.command("quit")

    départ = alice.receive()["payload"]
    assert départ["event"] == LEAVE
    assert départ["username"] == "carol"


def test_quit_ferme_la_connexion(connect):
    bob = connect("bob")
    bob.drain()

    bob.command("quit")

    assert bob.receive() is None


def test_commande_inconnue_répond_une_erreur_sans_fermer(connect):
    bob = connect("bob")
    bob.drain()

    bob.command("dance")

    assert bob.receive()["payload"]["event"] == ERROR
    bob.command("users")
    assert bob.receive()["payload"]["event"] == USER_LIST


def test_une_commande_n_est_jamais_rediffusée_aux_autres(connect):
    alice = connect("alice")
    bob = connect("bob")
    alice.drain()

    bob.command("users")
    bob.receive()
    bob.command("dance")
    bob.receive()

    assert alice.drain() == []


def test_le_pseudo_est_libéré_au_départ(connect):
    bob = connect("bob")
    bob.drain()
    bob.command("quit")
    bob.receive()

    carol = connect("bob")
    carol.drain()
    carol.command("users")
    assert carol.receive()["payload"]["users"] == ["bob"]


def relais_orphelin(monkeypatch, message: dict) -> list:
    """Relaie `message` pour une connexion absente du registre, et rend ce qui
    aurait été diffusé. La socket est un socketpair utilisable, pour qu'un envoi
    ne casse pas le test avant les assertions."""
    diffusés = []
    monkeypatch.setattr(
        server, "broadcast", lambda *args, **kwargs: diffusés.append(args)
    )
    orpheline, _autre_bout = socket.socketpair()
    assert orpheline not in server.clients

    server.relay_messages(orpheline, iter([message]))
    return diffusés


def test_un_chat_sans_pseudo_n_est_pas_diffusé(monkeypatch):
    """Un broadcast en échec ailleurs peut nous sortir du registre entre deux
    messages ; le suivant ne doit pas partir attribué à personne."""
    diffusés = relais_orphelin(monkeypatch, {"type": "chat", "payload": {"text": "salut"}})
    assert diffusés == []


def test_un_nick_ne_ressuscite_pas_une_connexion_retirée(monkeypatch):
    """claim_username() écrit dans le registre : sans garde-fou, /nick y
    réinscrirait une connexion que broadcast() venait d'en retirer."""
    avant = len(server.clients)
    diffusés = relais_orphelin(
        monkeypatch,
        {"type": "command", "payload": {"name": "nick", "args": ["intrus"]}},
    )
    assert diffusés == []
    assert len(server.clients) == avant
    assert "intrus" not in server.clients.values()
