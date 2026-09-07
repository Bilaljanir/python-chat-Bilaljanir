import argparse
import math
import re
import socket
import threading
from collections.abc import Iterator

from rich.console import Console

from protocol import ASK, ERR, OK, LineReader, MessageTooLong, send_line

console = Console()

MAX_CLIENTS = 50
IDLE_TIMEOUT = 300
MAX_NAME_ATTEMPTS = 3
NAME_PATTERN = re.compile(r"^[\w.-]{1,24}$")

def log_safely(message: str) -> None:
    """Journalise sans jamais lever : un pseudo peut être inaffichable ici.

    L'échappement est fait avant l'écriture, pas en rattrapant l'erreur : rich
    garde le texte fautif dans son tampon et le réémettrait au message suivant.
    """
    encoding = getattr(console.file, "encoding", None) or "utf-8"
    try:
        console.log(message.encode(encoding, "backslashreplace").decode(encoding))
    except Exception:
        pass


clients: dict[socket.socket, str] = {}
clients_lock = threading.Lock()


def release_username(sock: socket.socket) -> None:
    with clients_lock:
        clients.pop(sock, None)


def drop_client(sock: socket.socket) -> None:
    release_username(sock)
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def broadcast(message: str, sender: socket.socket | None = None) -> None:
    """Envoie une ligne à tous les clients sauf l'expéditeur."""
    data = f"{message}\n".encode()
    with clients_lock:
        targets = [sock for sock in clients if sock is not sender]

    unreachable = [sock for sock in targets if not try_send(sock, data)]
    for sock in unreachable:
        drop_client(sock)


def try_send(sock: socket.socket, data: bytes) -> bool:
    try:
        sock.sendall(data)
    except OSError:
        return False
    return True

def claim_username(conn: socket.socket, name: str) -> str | None:

    if not NAME_PATTERN.match(name):
        return "Pseudo invalide : 1 à 24 caractères, lettres, chiffres, . _ -"
    with clients_lock:
        if any(taken.casefold() == name.casefold() for taken in clients.values()):
            return "Ce pseudo est déjà utilisé, choisissez-en un autre."
        clients[conn] = name
    return None


def negotiate_username(conn: socket.socket, lines: Iterator[str]) -> str | None:

    for _ in range(MAX_NAME_ATTEMPTS):
        send_line(conn, f"{ASK} Choisissez un pseudo")
        proposal = next(lines, None)
        if proposal is None:
            return None

        name = proposal.strip()
        refusal = claim_username(conn, name)
        if refusal is None:
            try:
                send_line(conn, f"{OK} {name}")
            except OSError:
                release_username(conn)
                raise
            return name
        send_line(conn, f"{ERR} {refusal}")

    send_line(conn, f"{ERR} Trop de tentatives, connexion fermée.")
    return None

def handle_client(
    conn: socket.socket,
    address: tuple[str, int],
    sem: threading.Semaphore,
    idle_timeout: float,
) -> None:
    host, port = address
    username: str | None = None
    reader = LineReader(conn, idle_timeout)

    try:
        with conn:
            try:
                username = negotiate_username(conn, reader.lines())
                if username is not None:
                    log_safely(f"[green]Connected:[/] {username} ({host}:{port})")
                    broadcast(f"[{username}] a rejoint le chat", sender=conn)
                    relay_messages(conn, username, reader)
                if reader.timed_out:
                    send_line(conn, f"Déconnecté après {idle_timeout:g} s sans message.")
            except MessageTooLong as e:
                log_safely(f"[yellow]Message too long:[/] {host}:{port} ({e})")
            except (ConnectionError, TimeoutError, OSError):
                pass
    finally:
        try:
            if username is None:
                log_safely(f"[red]Rejected:[/] {host}:{port}")
            else:
                release_username(conn)
                broadcast(f"[{username}] a quitté le chat", sender=conn)
                log_safely(f"[red]Disconnected:[/] {username} ({host}:{port})")
        finally:
            sem.release()

def relay_messages(conn: socket.socket, username: str, reader: LineReader) -> None:
    for line in reader.lines():
        if line:
            broadcast(f"[{username}]: {line}", sender=conn)


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a finite number greater than 0 (got {value!r})"
        )
    return number


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0 (got {value!r})")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCP chat server")
    parser.add_argument(
        "--port", "-p", type=int, default=12345, help="Port to listen on"
    )
    parser.add_argument(
        "--max-clients",
        "-m",
        type=positive_int,
        default=MAX_CLIENTS,
        help="Max simultaneous connections",
    )
    parser.add_argument(
        "--idle-timeout",
        "-t",
        type=positive_float,
        default=IDLE_TIMEOUT,
        help="Seconds without a message before a client is disconnected",
    )
    return parser.parse_args()

def serve(server_socket: socket.socket, max_clients: int, idle_timeout: float) -> None:
    sem = threading.Semaphore(max_clients)
    while True:
        sem.acquire()
        conn, address = server_socket.accept()
        threading.Thread(
            target=handle_client,
            args=(conn, address, sem, idle_timeout),
            daemon=True,
        ).start()
        console.log(f"[blue]Active connections:[/] {threading.active_count() - 1}")


def main() -> None:
    args = parse_args()
    host = "0.0.0.0"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, args.port))
        server_socket.listen()
        console.log(
            f"[bold]Server listening on[/] {host}:{args.port}"
            f" [dim](max {args.max_clients} clients)[/]"
        )
        try:
            serve(server_socket, args.max_clients, args.idle_timeout)
        except KeyboardInterrupt:
            console.log("[yellow]Shutting down server...[/]")


if __name__ == "__main__":
    main()
