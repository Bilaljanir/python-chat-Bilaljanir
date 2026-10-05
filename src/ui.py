"""L'affichage du client avec Rich : messages, panneaux et invite de saisie."""

import sys
import threading
import time
import zlib

from rich.box import SIMPLE
from rich.console import Console, RenderableType
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
PRIVATE_STYLE = "magenta"
SEPARATOR = " -- "

_lock = threading.RLock()
_prompt_shown = False


def readline_active() -> bool:
    """Vrai si readline est disponible et que l'entrée est un terminal."""
    return readline is not None and sys.stdin.isatty()


def input_prompt() -> str:
    """Renvoie l'invite adaptée au terminal."""
    if readline_active():
        return READLINE_PROMPT
    if console.is_terminal:
        return RAW_PROMPT
    return PROMPT


def user_style(username: str) -> str:
    """Choisit une couleur stable pour un pseudo."""
    return USER_COLORS[zlib.crc32(username.casefold().encode()) % len(USER_COLORS)]


def timestamp(when: float | None = None) -> str:
    """Formate une heure en HH:MM:SS ; l'heure actuelle par défaut."""
    return time.strftime("%H:%M:%S", time.localtime(when))


def _line(
    label: str,
    text: str,
    *,
    label_style: str,
    text_style: str | None = None,
    mine: bool = False,
    when: float | None = None,
) -> Text:
    line = Text(justify="right") if mine else Text()
    if mine:
        line.append(text, style=text_style)
        line.append(SEPARATOR, style="dim")
        line.append(label, style=label_style)
        line.append(f" {timestamp(when)}", style="dim")
        return line

    line.append(timestamp(when), style="dim")
    line.append("  ")
    line.append(label, style=label_style)
    line.append(SEPARATOR, style="dim")
    line.append(text, style=text_style)
    return line


def chat_line(username: str, text: str, *, mine: bool = False) -> Text:
    """Met en forme un message public, aligné à droite si c'est le nôtre."""
    return _line(
        username,
        text,
        label_style=OWN_STYLE if mine else f"bold {user_style(username)}",
        mine=mine,
    )


def private_line(other: str, text: str, *, mine: bool = False) -> Text:
    """Met en forme un message privé envoyé ou reçu."""
    return _line(
        f"privé → {other}" if mine else f"privé ← {other}",
        text,
        label_style=f"bold {PRIVATE_STYLE}",
        text_style=PRIVATE_STYLE,
        mine=mine,
    )


def history_line(username: str, text: str, when: float | None = None) -> Text:
    """Met en forme un message de l'historique, en atténué."""
    return _line(
        username,
        text,
        label_style=f"dim {user_style(username)}",
        text_style="dim",
        when=when,
    )


def system_line(text: str, style: str) -> Text:
    """Met en forme une notification du serveur."""
    line = Text()
    line.append(timestamp(), style="dim")
    line.append("  · ", style="dim")
    line.append(text, style=style)
    return line


def help_panel(title: str, rows: list[tuple[str, str]], footer: str) -> Panel:
    """Construit le panneau d'aide des commandes."""
    table = Table(box=SIMPLE, show_header=False, pad_edge=False, padding=(0, 2, 0, 0))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="cyan")
    for usage, description in rows:
        table.add_row(usage, description)
    table.add_row("", Text(footer, style="dim italic"))
    return Panel(table, title=title, border_style="cyan", title_align="left")


def banner(text: str) -> Panel:
    """Construit le bandeau de bienvenue."""
    return Panel(Text(text, style="green"), border_style="green", expand=False)


def emit(renderable: RenderableType) -> None:
    """Affiche un élément sans casser la ligne en cours de saisie."""
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
    """Affiche une ligne de séparation avec un titre."""
    with _lock:
        console.rule(Text(title, style="dim"), style="dim")


def awaiting_input() -> bool:
    """Vrai si l'invite de saisie est affichée."""
    with _lock:
        return _prompt_shown


def read_line(prompt: str | None = None) -> str:
    """Lit une ligne au clavier en mémorisant que l'invite est affichée."""
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
