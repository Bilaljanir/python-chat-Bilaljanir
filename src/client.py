import argparse
import os
import signal
import socket
import threading
from collections.abc import Iterator

from rich.console import Console

from protocol import (
    ASK_USERNAME,
    CHAT,
    ERROR,
    JOIN,
    LEAVE,
    MAX_TEXT_LEN,
    NICK,
    SYSTEM,
    WELCOME,
    InvalidMessage,
    LineReader,
    MessageTooLong,
    chat_message,
    command_message,
    iter_messages,
    send_message,
)

console = Console()

SYSTEM_STYLES = {ERROR: "red", JOIN: "cyan", LEAVE: "cyan", WELCOME: "green"}


def show(line: str, style: str | None = None) -> None:
    console.print(line, style=style, markup=False, highlight=False)


def report_invalid(line: str, error: InvalidMessage) -> None:
    show(f"[message ignoré : {error}]", style="dim yellow")


def display(message: dict) -> None:
    payload = message["payload"]
    if message["type"] == CHAT:
        show(f"[{payload.get('username', '?')}]: {payload['text']}")
    elif message["type"] == SYSTEM:
        show(payload["text"], style=SYSTEM_STYLES.get(payload["event"], "yellow"))

def choose_username(sock: socket.socket, messages: Iterator[dict]) -> str | None:
    for message in messages:
        if message["type"] != SYSTEM:
            continue
        payload = message["payload"]
        event = payload["event"]

        if event == ASK_USERNAME:
            try:
                proposal = input(f"{payload['text']} : ").strip()
            except (KeyboardInterrupt, EOFError):
                return None
            send_message(sock, command_message(NICK, proposal))
        elif event == WELCOME:
            show(payload["text"], style="green")
            return payload.get("username", "")
        else:
            display(message)
    return None

def receive_messages(messages: Iterator[dict], stop: threading.Event) -> None:
    try:
        for message in messages:
            if stop.is_set():
                return
            display(message)
    except (OSError, MessageTooLong):
        pass

    if stop.is_set():
        return
    console.print("Connexion fermée par le serveur", style="yellow")
    stop.set()
    interrupt_input()

def interrupt_input() -> None:
    try:
        os.kill(os.getpid(), signal.SIGINT)
    except (AttributeError, OSError, ValueError):
        os._exit(0)


def send_user_input(sock: socket.socket, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            text = input().strip()
            if not text:
                continue
            if len(text) > MAX_TEXT_LEN:
                show(
                    f"Message trop long ({len(text)} > {MAX_TEXT_LEN} caractères),"
                    " rien n'a été envoyé.",
                    style="red",
                )
                continue
            send_message(sock, chat_message(text))
    except (KeyboardInterrupt, EOFError):
        console.print("\nDéconnecté.", style="yellow")
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
            sock.connect((args.host, args.port))
            console.print(f"Connecté à {args.host}:{args.port}", style="green")

            messages = iter_messages(LineReader(sock), report_invalid)
            if choose_username(sock, messages) is None:
                console.print("Connexion refusée par le serveur.", style="yellow")
                return

            stop = threading.Event()
            threading.Thread(
                target=receive_messages, args=(messages, stop), daemon=True
            ).start()
            send_user_input(sock, stop)
    except OSError as e:
        console.print(f"Connexion impossible : {e}", style="red")
    except MessageTooLong as e:
        console.print(f"Le serveur a envoyé une ligne trop longue : {e}", style="red")

if __name__ == "__main__":
    main()
