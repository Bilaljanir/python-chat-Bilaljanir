import argparse
import logging
import math
import select
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterator

from rich.console import Console, RenderableType

import admin
import registry
from protocol import (
    ASK_USERNAME,
    CHAT,
    COMMAND,
    ERROR,
    JOIN,
    LEAVE,
    MAX_MESSAGE_LEN,
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
    enable_keepalive,
    encode,
    iter_messages,
    send_message,
    system_message,
)

console = Console()
logger = logging.getLogger(admin.LOGGER_NAME)

MAX_CLIENTS = 50
IDLE_TIMEOUT = 300
MAX_NAME_ATTEMPTS = 3
RETRY_DELAY = 0.1
ACCEPT_TIMEOUT = 0.5
SEND_TIMEOUT = 5.0
MAX_INVALID_MESSAGES = 10
LOG_EXCERPT_LEN = 120
SHUTDOWN_NOTICE = "Le serveur s'arrête, à bientôt."
TOO_MANY_INVALID = "Trop de messages invalides, connexion fermée."
LINE_TOO_LONG = f"Ligne de plus de {MAX_MESSAGE_LEN} caractères, connexion fermée."


class TooManyInvalidMessages(Exception):
    """Un client a dépassé son quota de messages illisibles."""

def drop_client(sock: socket.socket) -> None:
    registry.release(sock)
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def shutdown_clients() -> int:
    socks = registry.drain()
    data = f"{encode(system_message(NOTICE, SHUTDOWN_NOTICE))}\n".encode()
    warned = sum(try_send(sock, data) for sock in socks)
    for sock in socks:
        drop_client(sock)
    return warned

def broadcast(message: dict, sender: socket.socket | None = None) -> None:
    data = f"{encode(message)}\n".encode()
    unreachable = [
        sock for sock in registry.targets(exclude=sender) if not try_send(sock, data)
    ]
    for sock in unreachable:
        drop_client(sock)

def try_send(sock: socket.socket, data: bytes) -> bool:

    try:
        _, writable, _ = select.select((), (sock,), (), SEND_TIMEOUT)
        if not writable:
            return False
        sock.sendall(data)
    except (OSError, ValueError):
        return False
    return True


def warn_client(sock: socket.socket, text: str) -> None:
    try_send(sock, f"{encode(system_message(ERROR, text))}\n".encode())

class InvalidMessageGuard:

    def __init__(
        self,
        conn: socket.socket,
        address: tuple[str, int],
        limit: int = MAX_INVALID_MESSAGES,
    ) -> None:
        self._conn = conn
        self._host, self._port = address
        self._limit = limit
        self.count = 0

    def __call__(self, line: str, error: InvalidMessage) -> None:
        self.count += 1
        logger.warning(
            "Message invalide de %s:%s (%d/%d) : %s — %s",
            self._host,
            self._port,
            self.count,
            self._limit,
            error,
            line[:LOG_EXCERPT_LEN],
        )
        if self.count >= self._limit:
            raise TooManyInvalidMessages(f"{self.count} messages invalides")
        warn_client(self._conn, f"Message ignoré : {error}")

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

        refusal = registry.claim(conn, name)
        if refusal is None:
            try:
                send_message(
                    conn,
                    system_message(
                        WELCOME, f"Connecté en tant que {name}", username=name
                    ),
                )
            except OSError:
                registry.release(conn)
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
    messages = iter_messages(reader, InvalidMessageGuard(conn, address))

    try:
        with conn:
            try:
                username = negotiate_username(conn, messages)
                if username is not None:
                    logger.info("Connexion : %s (%s:%s)", username, host, port)
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
                logger.warning("Ligne trop longue de %s:%s : %s", host, port, e)
                warn_client(conn, LINE_TOO_LONG)
            except TooManyInvalidMessages as e:
                logger.warning("Client incompris coupé %s:%s : %s", host, port, e)
                warn_client(conn, TOO_MANY_INVALID)
            except (ConnectionError, TimeoutError, OSError):
                pass
            except Exception:
                logger.exception("Erreur inattendue avec %s:%s", host, port)
    finally:
        try:
            if username is None:
                logger.info("Connexion refusée : %s:%s", host, port)
            else:
                username = registry.current(conn) or username
                registry.release(conn)
                broadcast(
                    system_message(
                        LEAVE, f"{username} a quitté le chat", username=username
                    ),
                    sender=conn,
                )
                logger.info("Déconnexion : %s (%s:%s)", username, host, port)
        except Exception:
            logger.exception("Nettoyage incomplet pour %s:%s", host, port)
        finally:
            sem.release()

def relay_messages(conn: socket.socket, messages: Iterator[dict]) -> None:
    for message in messages:
        username = registry.current(conn)
        if not username:
            return
        if not relay_one(conn, username, message):
            return

def relay_one(conn: socket.socket, username: str, message: dict) -> bool:
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
    broadcast(chat_message(text, username=username), sender=conn)
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
    names = registry.usernames()
    listed = ", ".join(names) if names else "personne"
    return system_message(
        USER_LIST, f"Connectés ({len(names)}) : {listed}", users=names
    )

def rename(conn: socket.socket, new_name: str) -> None:
    old_name = registry.current(conn)
    if new_name == old_name:
        send_message(conn, system_message(ERROR, "C'est déjà votre pseudo"))
        return

    refusal = registry.claim(conn, new_name)
    if refusal is not None:
        send_message(conn, system_message(ERROR, refusal))
        return

    logger.info("Renommage : %s → %s", old_name, new_name)
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
    parser.add_argument(
        "--log-file",
        "-l",
        default=admin.LOG_FILE,
        help="File the activity log is appended to",
    )
    parser.add_argument(
        "--no-dashboard",
        dest="dashboard",
        action="store_false",
        help="Stream the log to the terminal instead of drawing the dashboard",
    )
    return parser.parse_args()

def serve(
    server_socket: socket.socket,
    max_clients: int,
    idle_timeout: float,
    stop: threading.Event | None = None,
) -> None:
    sem = threading.Semaphore(max_clients)
    stop = stop or threading.Event()
    server_socket.settimeout(ACCEPT_TIMEOUT)
    while not stop.is_set():
        if not sem.acquire(timeout=ACCEPT_TIMEOUT):
            continue
        conn = None
        started = False
        try:
            if stop.is_set():
                break
            conn, address = server_socket.accept()
            enable_keepalive(conn)
            threading.Thread(
                target=handle_client,
                args=(conn, address, sem, idle_timeout),
                daemon=True,
            ).start()
            started = True
        except TimeoutError:
            continue
        except (OSError, RuntimeError) as e:
            if server_socket.fileno() == -1:
                break
            logger.warning("Connexion abandonnée : %s", e)
            time.sleep(RETRY_DELAY)
        except Exception:
            logger.exception("Accueil en échec, la boucle continue")
            time.sleep(RETRY_DELAY)
        finally:
            if not started:
                sem.release()
                if conn is not None:
                    conn.close()
        logger.debug("Threads actifs : %d", threading.active_count() - 1)


def install_stop_handlers(stop: threading.Event) -> None:

    def request_stop(signum: int, _frame: object) -> None:
        signal.signal(signum, signal.SIG_DFL)
        stop.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)


def dashboard_snapshot(
    address: str, max_clients: int, activity: admin.ActivityLog
) -> Callable[[], RenderableType]:
    started = time.monotonic()

    def snapshot() -> RenderableType:
        return admin.dashboard(
            address,
            registry.usernames(),
            max_clients,
            time.monotonic() - started,
            activity.lines(),
        )

    return snapshot


def main() -> None:
    args = parse_args()
    host = "0.0.0.0"
    address = f"{host}:{args.port}"
    drawing = args.dashboard and console.is_terminal

    activity = admin.setup_logging(
        args.log_file,
        console=console,
        console_level=logging.WARNING if drawing else logging.INFO,
    )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, args.port))
        server_socket.listen()
        logger.info(
            "Serveur à l'écoute sur %s (max %d clients, journal %s)",
            address,
            args.max_clients,
            args.log_file,
        )
        stop = threading.Event()
        install_stop_handlers(stop)
        threading.Thread(
            target=serve,
            args=(server_socket, args.max_clients, args.idle_timeout, stop),
            daemon=True,
        ).start()
        try:
            if drawing:
                snapshot = dashboard_snapshot(address, args.max_clients, activity)
                admin.run_dashboard(console, snapshot, stop)
            else:
                stop.wait()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            logger.info("Arrêt demandé, %d client(s) prévenu(s)", shutdown_clients())


if __name__ == "__main__":
    main()
