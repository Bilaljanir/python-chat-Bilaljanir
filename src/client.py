import argparse
import socket
import threading

from rich.console import Console

console = Console()


def receive_messages(sock: socket.socket, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            data = sock.recv(1024)
            if not data:
                console.log("[yellow]Server closed the connection.[/]")
                break
            console.log(data.decode())
    except OSError:
        if not stop.is_set():
            console.log("[red]Connection lost.[/]")


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP chat client")
    parser.add_argument("host", nargs="?", default="localhost", help="Server host")
    parser.add_argument("port", nargs="?", type=int, default=12345, help="Server port")
    args = parser.parse_args()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect((args.host, args.port))
            console.log(f"[green]Connected to[/] {args.host}:{args.port}")

            stop = threading.Event()
            thread = threading.Thread(
                target=receive_messages, args=(sock, stop), daemon=True
            )
            thread.start()

            try:
                while True:
                    message = input()
                    if not message:
                        continue
                    sock.sendall(message.encode())
            except (KeyboardInterrupt, EOFError):
                console.log("\n[yellow]Disconnected.[/]")
            finally:
                stop.set()
    except OSError as e:
        console.log(f"[red]Could not connect:[/] {e}")

if __name__ == "__main__":
    main()