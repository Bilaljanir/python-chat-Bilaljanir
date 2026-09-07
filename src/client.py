import argparse
import os
import signal
import socket
import threading
from collections.abc import Iterator

from rich.console import Console

from protocol import ASK, ERR, OK, LineReader, send_line, split_tag

console = Console()


def show(line: str) -> None:
    console.print(line, markup=False, highlight=False)


def choose_username(sock: socket.socket, lines: Iterator[str]) -> str | None:
    for line in lines:
        tag, rest = split_tag(line)
        if tag == ASK:
            try:
                proposal = input(f"{rest} : ").strip()
            except (KeyboardInterrupt, EOFError):
                return None
            send_line(sock, proposal)
        elif tag == ERR:
            console.print(rest, style="red", markup=False, highlight=False)
        elif tag == OK:
            console.print(f"Connecté en tant que {rest}", style="green")
            return rest
    return None

def receive_messages(lines: Iterator[str], stop: threading.Event) -> None:
    try:
        for line in lines:
            if stop.is_set():
                return
            show(line)
    except OSError:
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
            message = input()
            if message:
                send_line(sock, message)
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

            lines = LineReader(sock).lines()
            if choose_username(sock, lines) is None:
                console.print("Connexion refusée par le serveur.", style="yellow")
                return

            stop = threading.Event()
            threading.Thread(
                target=receive_messages, args=(lines, stop), daemon=True
            ).start()
            send_user_input(sock, stop)
    except OSError as e:
        console.print(f"Connexion impossible : {e}", style="red")

if __name__ == "__main__":
    main()