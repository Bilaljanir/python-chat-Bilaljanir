import argparse
import socket
import threading

from rich.console import Console

console = Console()


def handle_client(conn: socket.socket, address: tuple[str, int]) -> None:
    host, port = address
    console.log(f"[green]Connected:[/] {host}:{port}")
    with conn:
        try:
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                conn.sendall(data)
        except ConnectionError:
            pass
    console.log(f"[red]Disconnected:[/] {host}:{port}")


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP chat server")
    parser.add_argument(
        "--port", "-p", type=int, default=12345, help="Port to listen on"
    )
    args = parser.parse_args()

    host = "0.0.0.0"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, args.port))
        server_socket.listen()
        console.log(f"[bold]Server listening on[/] {host}:{args.port}")
        try:
            while True:
                conn, address = server_socket.accept()
                thread = threading.Thread(
                    target=handle_client, args=(conn, address), daemon=True
                )
                thread.start()
                console.log(
                    f"[blue]Active connections:[/] {threading.active_count() - 1}"
                )
        except KeyboardInterrupt:
            console.log("[yellow]Shutting down server...[/]")


if __name__ == "__main__":
    main()