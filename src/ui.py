import sys
import threading
import time
import zlib

from rich.box import SIMPLE
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    import readline
except ImportError:
    readline = None

console = Console(highlight=False, markup=False)

PROMPT = "❯ "
_CYAN = "\x1b[1;36m"
_RESET = "\x1b[0m"
READLINE_PROMPT = f"\001{_CYAN}\002{PROMPT}\001{_RESET}\002"
RAW_PROMPT = f"{_CYAN}{PROMPT}{_RESET}"

USER_COLORS = (
    "cyan",
    "magenta",
    "green",
    "yellow",
    "blue",
    "bright_magenta",
    "bright_green",
    "bright_yellow",
)

OWN_STYLE = "bold bright_white"
SEPARATOR = " -- "

_lock = threading.RLock()
_prompt_shown = False


def readline_active() -> bool:
    return readline is not None and sys.stdin.isatty()


def input_prompt() -> str:
    if readline_active():
        return READLINE_PROMPT
    if console.is_terminal:
        return RAW_PROMPT
    return PROMPT


def user_style(username: str) -> str:
    return USER_COLORS[zlib.crc32(username.casefold().encode()) % len(USER_COLORS)]


def timestamp(when: float | None = None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(when))


def chat_line(username: str, text: str, *, mine: bool = False, when: float | None = None) -> Text:
    line = Text(justify="right") if mine else Text()
    if mine:
        line.append(text)
        line.append(SEPARATOR, style="dim")
        line.append(username, style=OWN_STYLE)
        line.append(f" {timestamp(when)}", style="dim")
        return line

    line.append(timestamp(when), style="dim")
    line.append("  ")
    line.append(username, style=f"bold {user_style(username)}")
    line.append(SEPARATOR, style="dim")
    line.append(text)
    return line


def system_line(text: str, style: str, when: float | None = None) -> Text:
    line = Text()
    line.append(timestamp(when), style="dim")
    line.append("  · ", style="dim")
    line.append(text, style=style)
    return line


def help_panel(title: str, rows: list[tuple[str, str]], footer: str) -> Panel:
    table = Table(box=SIMPLE, show_header=False, pad_edge=False, padding=(0, 2, 0, 0))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="cyan")
    for usage, description in rows:
        table.add_row(usage, description)
    table.add_row("", Text(footer, style="dim italic"))
    return Panel(table, title=title, border_style="cyan", title_align="left")


def banner(text: str, style: str = "green") -> Panel:
    return Panel(Text(text, style=style), border_style=style, expand=False)


def emit(renderable) -> None:

    justify = getattr(renderable, "justify", None)
    with _lock:
        redraw = _prompt_shown and readline_active()
        if redraw:
            sys.stdout.write("\r\x1b[2K")
            sys.stdout.flush()
        console.print(renderable, justify=justify)
        if redraw:
            sys.stdout.write(RAW_PROMPT + readline.get_line_buffer())
            sys.stdout.flush()


def separator(title: str = "") -> None:
    with _lock:
        console.rule(Text(title, style="dim"), style="dim")


def awaiting_input() -> bool:
    with _lock:
        return _prompt_shown


def read_line(prompt: str | None = None) -> str:

    global _prompt_shown
    if prompt is None:
        prompt = input_prompt()
    with _lock:
        _prompt_shown = True
    try:
        return input(prompt)
    finally:
        with _lock:
            _prompt_shown = False
