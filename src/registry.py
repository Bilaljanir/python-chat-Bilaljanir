"""Le registre des clients connectés : le seul état partagé entre les threads."""

import re
import socket
import threading

NAME_PATTERN = re.compile(r"^[\w.-]{1,24}$")
INVALID_NAME = "Pseudo invalide : 1 à 24 caractères, lettres, chiffres, . _ -"
NAME_TAKEN = "Ce pseudo est déjà utilisé, choisissez-en un autre."

clients: dict[socket.socket, str] = {}
_lock = threading.Lock()


def claim(conn: socket.socket, name: str) -> str | None:
    """Attribue un pseudo à une connexion ; renvoie le motif du refus, ou None."""
    if not NAME_PATTERN.match(name):
        return INVALID_NAME
    with _lock:
        if any(
            sock is not conn and taken.casefold() == name.casefold()
            for sock, taken in clients.items()
        ):
            return NAME_TAKEN
        clients[conn] = name
    return None


def release(sock: socket.socket) -> None:
    """Retire une connexion du registre."""
    with _lock:
        clients.pop(sock, None)


def find(name: str) -> tuple[socket.socket, str] | None:
    """Cherche un client par pseudo, sans tenir compte de la casse."""
    wanted = name.casefold()
    with _lock:
        for sock, taken in clients.items():
            if taken.casefold() == wanted:
                return sock, taken
    return None


def current(conn: socket.socket) -> str:
    """Renvoie le pseudo d'une connexion, ou une chaîne vide."""
    with _lock:
        return clients.get(conn, "")


def usernames() -> list[str]:
    """Renvoie les pseudos connectés, triés par ordre alphabétique."""
    with _lock:
        return sorted(clients.values(), key=str.casefold)


def targets(exclude: socket.socket | None = None) -> list[socket.socket]:
    """Renvoie une copie des connexions, sauf celle à exclure."""
    with _lock:
        return [sock for sock in clients if sock is not exclude]


def drain() -> list[socket.socket]:
    """Vide le registre et renvoie toutes les connexions qu'il contenait."""
    with _lock:
        socks = list(clients)
        clients.clear()
    return socks
