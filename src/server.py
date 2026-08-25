import argparse
import socket
import threading
import time

from rich.console import Console

console = Console()

MAX_CLIENTS = 50


def handle_client(
    conn: socket.socket, address: tuple[str, int], sem: threading.Semaphore
) -> None:
    host, port = address
    console.log(f"[green]Connected:[/] {host}:{port}")
    deadline = time.monotonic() + 10
    with conn:
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                conn.settimeout(remaining)
                data = conn.recv(1024)
                if not data:
                    break
                conn.sendall(data)
                deadline = time.monotonic() + 10
        except (ConnectionError, TimeoutError):
            pass
    sem.release()
    console.log(f"[red]Disconnected:[/] {host}:{port}")


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP chat server")
    parser.add_argument(
        "--port", "-p", type=int, default=12345, help="Port to listen on"
    )
    parser.add_argument(
        "--max-clients",
        "-m",
        type=int,
        default=MAX_CLIENTS,
        help="Max simultaneous connections",
    )
    args = parser.parse_args()

    host = "0.0.0.0"
    sem = threading.Semaphore(args.max_clients)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, args.port))
        server_socket.listen()
        console.log(
            f"[bold]Server listening on[/] {host}:{args.port}"
            f" [dim](max {args.max_clients} clients)[/]"
        )
        try:
            while True:
                sem.acquire()
                conn, address = server_socket.accept()
                thread = threading.Thread(
                    target=handle_client,
                    args=(conn, address, sem),
                    daemon=True,
                )
                thread.start()
                console.log(
                    f"[blue]Active connections:[/] {threading.active_count() - 1}"
                )
        except KeyboardInterrupt:
            console.log("[yellow]Shutting down server...[/]")


if __name__ == "__main__":
    main()
