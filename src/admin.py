import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from rich.box import SIMPLE
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

LOGGER_NAME = "chat.server"
LOG_FILE = "server.log"
FILE_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
CLOCK_FORMAT = "%H:%M:%S"

ACTIVITY_LINES = 12
REFRESH_DELAY = 0.5

LEVEL_STYLES = {
    logging.DEBUG: "dim",
    logging.INFO: "green",
    logging.WARNING: "yellow",
    logging.ERROR: "red",
    logging.CRITICAL: "bold red",
}
DEFAULT_LEVEL_STYLE = "white"

logger = logging.getLogger(LOGGER_NAME)


class ActivityLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._records: deque[tuple[float, int, str]] = deque(maxlen=ACTIVITY_LINES)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._records.append((record.created, record.levelno, record.getMessage()))
        except (TypeError, ValueError):
            self.handleError(record)

    def lines(self) -> list[tuple[float, int, str]]:
        return list(self._records)


def setup_logging(path: str, console: Console, console_level: int) -> ActivityLog:
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
    logger.addHandler(file_handler)

    console_handler = RichHandler(
        console=console,
        markup=False,
        show_path=False,
        rich_tracebacks=True,
        log_time_format=f"[{CLOCK_FORMAT}]",
    )
    console_handler.setLevel(console_level)
    logger.addHandler(console_handler)

    activity = ActivityLog()
    logger.addHandler(activity)
    return activity


def uptime(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _grid() -> Table:
    return Table(box=SIMPLE, show_header=False, pad_edge=False, padding=(0, 2, 0, 0))


def status_table(
    address: str, users: list[str], max_clients: int, elapsed: float
) -> Table:
    table = _grid()
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold")
    table.add_row("Écoute", address)
    table.add_row("Clients", f"{len(users)} / {max_clients}")
    table.add_row("En ligne", uptime(elapsed))
    table.add_row(
        "Connectés",
        Text(", ".join(users)) if users else Text("personne", style="dim italic"),
    )
    return table

def activity_table(lines: list[tuple[float, int, str]]) -> Table:
    table = _grid()
    table.add_column(style="dim", no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")
    if not lines:
        table.add_row("", "", Text("rien pour l'instant", style="dim italic"))
    for created, levelno, message in lines:
        table.add_row(
            time.strftime(CLOCK_FORMAT, time.localtime(created)),
            Text(
                logging.getLevelName(levelno),
                style=LEVEL_STYLES.get(levelno, DEFAULT_LEVEL_STYLE),
            ),
            Text(message),
        )
    return table

def dashboard(
    address: str,
    users: list[str],
    max_clients: int,
    elapsed: float,
    lines: list[tuple[float, int, str]],
) -> RenderableType:
    return Group(
        Panel(
            status_table(address, users, max_clients, elapsed),
            title="Serveur de chat",
            border_style="cyan",
            title_align="left",
        ),
        Panel(
            activity_table(lines),
            title="Activité récente",
            border_style="blue",
            title_align="left",
        ),
    )


def run_dashboard(
    console: Console,
    snapshot: Callable[[], RenderableType],
    stop: threading.Event,
) -> None:
    with Live(snapshot(), console=console, refresh_per_second=4) as live:
        while not stop.wait(REFRESH_DELAY):
            live.update(snapshot())
