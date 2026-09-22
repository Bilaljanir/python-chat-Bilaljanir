import argparse
import os
import signal
import socket
import threading
from collections.abc import Iterator

import ui
from protocol import (
    ASK_USERNAME,
    CHAT,
    ERROR,
    HELP,
    JOIN,
    LEAVE,
    MAX_TEXT_LEN,
    MSG,
    NICK,
    NOTICE,
    PRIVATE,
    QUIT,
    RENAME,
    SYSTEM,
    USER_LIST,
    USERS,
    WELCOME,
    InvalidMessage,
    LineReader,
    MessageTooLong,
    chat_message,
    command_message,
    enable_keepalive,
    iter_messages,
    private_message,
    send_message,
)

SYSTEM_STYLES = {
    ERROR: "italic red",
    JOIN: "italic green",
    LEAVE: "italic yellow",
    NOTICE: "italic yellow",
    RENAME: "italic cyan",
    USER_LIST: "cyan",
    WELCOME: "green",
}
DEFAULT_SYSTEM_STYLE = "italic yellow"

COMMAND_HELP = {
    HELP: ("/help", "affiche cette aide"),
    USERS: ("/users", "liste les utilisateurs connectés"),
    NICK: ("/nick <pseudo>", "change de pseudo"),
    MSG: ("/msg <pseudo> <message>", "message privé à une seule personne"),
    QUIT: ("/quit", "quitte le chat"),
}


class Session:

    def __init__(self, username: str) -> None:
        self._username = username
        self._lock = threading.Lock()

    @property
    def username(self) -> str:
        with self._lock:
            return self._username

    def rename(self, new_username: str) -> None:
        with self._lock:
            self._username = new_username

def show_system(text: str, style: str = DEFAULT_SYSTEM_STYLE) -> None:
    ui.emit(ui.system_line(text, style))


def report_invalid(line: str, error: InvalidMessage) -> None:
    show_system(f"message ignoré : {error}", style="dim italic yellow")


def display(message: dict, session: Session) -> None:
    payload = message["payload"]
    if message["type"] == CHAT:
        ui.emit(ui.chat_line(payload.get("username", "?"), payload["text"]))
    elif message["type"] == PRIVATE:
        display_private(payload, session)
    elif message["type"] == SYSTEM:
        track_rename(payload, session)
        show_system(
            payload["text"], SYSTEM_STYLES.get(payload["event"], DEFAULT_SYSTEM_STYLE)
        )


def display_private(payload: dict, session: Session) -> None:

    sender = payload.get("username", "?")
    mine = sender == session.username
    other = payload["to"] if mine else sender
    ui.emit(ui.private_line(other, payload["text"], mine=mine))

def track_rename(payload: dict, session: Session) -> None:

    if payload["event"] != RENAME or payload.get("username") != session.username:
        return
    new_username = payload.get("new_username")
    if new_username:
        session.rename(new_username)


def show_help() -> None:
    ui.emit(
        ui.help_panel(
            "Commandes",
            list(COMMAND_HELP.values()),
            "toute autre ligne est envoyée comme message",
        )
    )


def parse_command(text: str) -> tuple[str, list[str]]:
    name, _, rest = text[1:].partition(" ")
    return name.casefold(), rest.split()


def run_command(sock: socket.socket, text: str, stop: threading.Event) -> bool:
    name, args = parse_command(text)
    if name == HELP:
        show_help()
    elif name == QUIT:
        stop.set()
        send_message(sock, command_message(QUIT))
        show_system("Déconnecté.", style="italic yellow")
        return False
    elif name == USERS:
        send_message(sock, command_message(USERS))
    elif name == NICK:
        if len(args) != 1:
            show_system("Usage : /nick <pseudo>", style="italic red")
        else:
            send_message(sock, command_message(NICK, args[0]))
    elif name == MSG:
        send_private(sock, text)
    else:
        show_system(f"Commande inconnue : /{name} — tapez /help", style="italic red")
    return True

def send_private(sock: socket.socket, text: str) -> None:

    _, _, rest = text[1:].partition(" ")
    target, _, body = rest.strip().partition(" ")
    body = body.strip()
    if not target or not body:
        show_system("Usage : /msg <pseudo> <message>", style="italic red")
        return
    if len(body) > MAX_TEXT_LEN:
        show_system(
            f"Message trop long ({len(body)} > {MAX_TEXT_LEN} caractères),"
            " rien n'a été envoyé.",
            style="italic red",
        )
        return
    send_message(sock, private_message(body, to=target))

def ask_username(prompt: str) -> str | None:
    while True:
        try:
            text = ui.read_line(f"{prompt} {ui.input_prompt()}").strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not text:
            continue
        if not text.startswith("/"):
            return text

        name, args = parse_command(text)
        if name == QUIT:
            return None
        if name == HELP:
            show_help()
        elif name == NICK and len(args) == 1:
            return args[0]
        else:
            show_system("Ici, tapez un pseudo (ou /help, /quit).", style="italic red")

def choose_username(sock: socket.socket, messages: Iterator[dict]) -> str | None:
    for message in messages:
        if message["type"] != SYSTEM:
            continue
        payload = message["payload"]
        event = payload["event"]

        if event == ASK_USERNAME:
            proposal = ask_username(payload["text"])
            if proposal is None:
                show_system("Déconnecté.", style="italic yellow")
                return None
            send_message(sock, command_message(NICK, proposal))
        elif event == WELCOME:
            ui.emit(ui.banner(payload["text"]))
            show_system("Tapez /help pour la liste des commandes.", style="dim italic cyan")
            return payload.get("username", "")
        else:
            show_system(payload["text"], SYSTEM_STYLES.get(event, DEFAULT_SYSTEM_STYLE))

    show_system("Connexion refusée par le serveur.", style="italic yellow")
    return None

def receive_messages(
    messages: Iterator[dict], stop: threading.Event, session: Session
) -> None:
    reason = "Connexion fermée par le serveur"
    try:
        for message in messages:
            if stop.is_set():
                return
            display(message, session)
    except MessageTooLong as e:
        reason = f"Le serveur a envoyé une ligne trop longue : {e}"
    except OSError as e:
        reason = f"Connexion perdue : {e}"
    finally:
        announce_closed(stop, reason)

def announce_closed(stop: threading.Event, reason: str) -> None:

    if stop.is_set():
        return
    stop.set()
    show_system(reason, style="italic yellow")
    if ui.awaiting_input():
        interrupt_input()

def interrupt_input() -> None:
    try:
        os.kill(os.getpid(), signal.SIGINT)
    except (AttributeError, OSError, ValueError):
        os._exit(0)


def send_user_input(
    sock: socket.socket, stop: threading.Event, session: Session
) -> None:
    try:
        while not stop.is_set():
            text = ui.read_line().strip()
            if not text:
                continue
            if text.startswith("/"):
                if not run_command(sock, text, stop):
                    return
                continue
            if len(text) > MAX_TEXT_LEN:
                show_system(
                    f"Message trop long ({len(text)} > {MAX_TEXT_LEN} caractères),"
                    " rien n'a été envoyé.",
                    style="italic red",
                )
                continue
            send_message(sock, chat_message(text))
            ui.emit(ui.chat_line(session.username, text, mine=True))
    except (KeyboardInterrupt, EOFError):
        if not stop.is_set():
            ui.emit(ui.system_line("Déconnecté.", "italic yellow"))
    except OSError as e:
        if not stop.is_set():
            show_system(f"Connexion perdue : {e}", style="italic red")
    finally:
        stop.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCP chat client")
    parser.add_argument("host", nargs="?", default="localhost", help="Server host")
    parser.add_argument("port", nargs="?", type=int, default=12345, help="Server port")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.connect((args.host, args.port))
            except OSError as e:
                show_system(f"Connexion impossible : {e}", style="italic red")
                return
            enable_keepalive(sock)
            show_system(f"Connecté à {args.host}:{args.port}", style="italic green")

            messages = iter_messages(LineReader(sock), report_invalid)
            username = choose_username(sock, messages)
            if username is None:
                return

            session = Session(username)
            ui.separator("conversation")

            stop = threading.Event()
            threading.Thread(
                target=receive_messages,
                args=(messages, stop, session),
                daemon=True,
            ).start()
            send_user_input(sock, stop, session)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        show_system(f"Connexion perdue : {e}", style="italic red")
    except MessageTooLong as e:
        show_system(
            f"Le serveur a envoyé une ligne trop longue : {e}", style="italic red"
        )

if __name__ == "__main__":
    main()
