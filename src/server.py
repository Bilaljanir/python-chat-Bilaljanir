import argparse
import codecs
import socket
import threading
import time

from rich.console import Console

console = Console()

MAX_CLIENTS = 50
IDLE_TIMEOUT = 300

clients: dict[socket.socket, tuple[str, int]] = {}
clients_lock = threading.Lock()


def broadcast(message: str, sender: socket.socket | None = None) -> None:
    data = message.encode()
    with clients_lock:
        targets = [s for s in clients if s is not sender]
    dead = []
    for sock in targets:
        try:
            sock.sendall(data)
        except OSError:
            dead.append(sock)
    if dead:
        with clients_lock:
            for sock in dead:
                clients.pop(sock, None)


def handle_client(
    conn: socket.socket,
    address: tuple[str, int],
    sem: threading.Semaphore,
    idle_timeout: float,
) -> None:
    host, port = address
    with clients_lock:
        clients[conn] = address
    console.log(f"[green]Connected:[/] {host}:{port}")
    broadcast(f"[{host}:{port}] a rejoint le chat\n", sender=conn)

    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    deadline = time.monotonic() + idle_timeout
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
                deadline = time.monotonic() + idle_timeout
                text = decoder.decode(data)
                if not text:
                    continue
                broadcast(f"[{host}:{port}] {text}\n", sender=conn)
        except (ConnectionError, TimeoutError):
            pass

    with clients_lock:
        clients.pop(conn, None)
    broadcast(f"[{host}:{port}] a quitté le chat\n", sender=conn)
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
    parser.add_argument(
        "--idle-timeout",
        "-t",
        type=float,
        default=IDLE_TIMEOUT,
        help="Seconds without a message before a client is disconnected",
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
                    args=(conn, address, sem, args.idle_timeout),
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
