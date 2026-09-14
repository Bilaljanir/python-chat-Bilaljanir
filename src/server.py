import argparse
import math
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator

from rich.console import Console
from rich.markup import escape

from protocol import (
    ASK_USERNAME,
    CHAT,
    COMMAND,
    ERROR,
    JOIN,
    LEAVE,
    MAX_TEXT_LEN,
    NICK,
    NOTICE,
    QUIT,
    RENAME,
    USER_LIST,
    USERS,
    WELCOME,
    InvalidMessage,
    LineReader,
    MessageTooLong,
    chat_message,
    encode,
    iter_messages,
    send_message,
    system_message,
)

console = Console()

MAX_CLIENTS = 50
IDLE_TIMEOUT = 300
MAX_NAME_ATTEMPTS = 3
RETRY_DELAY = 0.1
LOG_EXCERPT_LEN = 120
NAME_PATTERN = re.compile(r"^[\w.-]{1,24}$")

def log_safely(message: str) -> None:
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


def broadcast(message: dict, sender: socket.socket | None = None) -> None:
    data = f"{encode(message)}\n".encode()
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
        # « sock is not conn » : un renommage ne doit pas buter sur son propre pseudo.
        if any(
            sock is not conn and taken.casefold() == name.casefold()
            for sock, taken in clients.items()
        ):
            return "Ce pseudo est déjà utilisé, choisissez-en un autre."
        clients[conn] = name
    return None


def current_username(conn: socket.socket) -> str:
    with clients_lock:
        return clients.get(conn, "")

def connected_usernames() -> list[str]:
    with clients_lock:
        return sorted(clients.values(), key=str.casefold)

def invalid_message_logger(
    address: tuple[str, int],
) -> Callable[[str, InvalidMessage], None]:
    host, port = address

    def report(line: str, error: InvalidMessage) -> None:
        excerpt = escape(line[:LOG_EXCERPT_LEN])
        log_safely(f"[yellow]Message invalide de[/] {host}:{port} : {error} — {excerpt}")

    return report


def negotiate_username(conn: socket.socket, messages: Iterator[dict]) -> str | None:

    for _ in range(MAX_NAME_ATTEMPTS):
        send_message(conn, system_message(ASK_USERNAME, "Choisissez un pseudo"))
        message = next(messages, None)
        if message is None:
            return None

        name = username_proposal(message)
        if name is None:
            send_message(
                conn,
                system_message(ERROR, "Envoyez la commande « nick <pseudo> »."),
            )
            continue

        refusal = claim_username(conn, name)
        if refusal is None:
            try:
                send_message(
                    conn,
                    system_message(
                        WELCOME, f"Connecté en tant que {name}", username=name
                    ),
                )
            except OSError:
                release_username(conn)
                raise
            return name
        send_message(conn, system_message(ERROR, refusal))

    send_message(conn, system_message(ERROR, "Trop de tentatives, connexion fermée."))
    return None

def username_proposal(message: dict) -> str | None:
    payload = message["payload"]
    if message["type"] != COMMAND or payload["name"] != NICK:
        return None
    name = payload["args"][0].strip() if payload["args"] else ""
    return name or None

def handle_client(
    conn: socket.socket,
    address: tuple[str, int],
    sem: threading.Semaphore,
    idle_timeout: float,
) -> None:
    host, port = address
    username: str | None = None
    reader = LineReader(conn, idle_timeout)
    messages = iter_messages(reader, invalid_message_logger(address))

    try:
        with conn:
            try:
                username = negotiate_username(conn, messages)
                if username is not None:
                    log_safely(f"[green]Connected:[/] {username} ({host}:{port})")
                    broadcast(
                        system_message(
                            JOIN, f"{username} a rejoint le chat", username=username
                        ),
                        sender=conn,
                    )
                    relay_messages(conn, messages)
                if reader.timed_out:
                    send_message(
                        conn,
                        system_message(
                            NOTICE,
                            f"Déconnecté après {idle_timeout:g} s sans message.",
                        ),
                    )
            except MessageTooLong as e:
                log_safely(f"[yellow]Message too long:[/] {host}:{port} ({e})")
            except (ConnectionError, TimeoutError, OSError):
                pass
    finally:
        try:
            if username is None:
                log_safely(f"[red]Rejected:[/] {host}:{port}")
            else:
                username = current_username(conn) or username
                release_username(conn)
                broadcast(
                    system_message(
                        LEAVE, f"{username} a quitté le chat", username=username
                    ),
                    sender=conn,
                )
                log_safely(f"[red]Disconnected:[/] {username} ({host}:{port})")
        finally:
            sem.release()

def relay_messages(conn: socket.socket, messages: Iterator[dict]) -> None:
    for message in messages:
        if not relay_one(conn, message):
            return


def relay_one(conn: socket.socket, message: dict) -> bool:
    payload = message["payload"]
    if message["type"] == COMMAND:
        return run_command(conn, payload["name"], payload["args"])
    if message["type"] != CHAT:
        send_message(conn, system_message(ERROR, "Type de message inattendu ici."))
        return True

    text = payload["text"].strip()
    if not text:
        return True
    if len(text) > MAX_TEXT_LEN:
        send_message(
            conn,
            system_message(ERROR, f"Message trop long (max {MAX_TEXT_LEN} caractères)."),
        )
        return True
    broadcast(chat_message(text, username=current_username(conn)), sender=conn)
    return True


def run_command(conn: socket.socket, name: str, args: list[str]) -> bool:
    if name == QUIT:
        return False
    if name == USERS:
        send_message(conn, user_list_message())
        return True
    if name == NICK:
        rename(conn, args[0].strip() if args else "")
        return True
    send_message(conn, system_message(ERROR, f"Commande inconnue : {name}"))
    return True


def user_list_message() -> dict:
    names = connected_usernames()
    listed = ", ".join(names) if names else "personne"
    return system_message(
        USER_LIST, f"Connectés ({len(names)}) : {listed}", users=names
    )

def rename(conn: socket.socket, new_name: str) -> None:
    old_name = current_username(conn)
    if new_name == old_name:
        send_message(conn, system_message(ERROR, "C'est déjà votre pseudo"))
        return

    refusal = claim_username(conn, new_name)
    if refusal is not None:
        send_message(conn, system_message(ERROR, refusal))
        return

    log_safely(f"[green]Renamed:[/] {old_name} → {new_name}")
    send_message(
        conn,
        system_message(
            RENAME,
            f"Vous êtes désormais {new_name}",
            username=old_name,
            new_username=new_name,
        ),
    )
    broadcast(
        system_message(
            RENAME,
            f"{old_name} est désormais {new_name}",
            username=old_name,
            new_username=new_name,
        ),
        sender=conn,
    )

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
        conn = None
        started = False
        try:
            conn, address = server_socket.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, address, sem, idle_timeout),
                daemon=True,
            ).start()
            started = True
        except (OSError, RuntimeError) as e:
            # Une connexion qui échoue ne doit pas emporter la boucle d'accueil.
            log_safely(f"[yellow]Connexion abandonnée :[/] {e}")
            time.sleep(RETRY_DELAY)
        finally:
            if not started:
                sem.release()
                if conn is not None:
                    conn.close()
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
