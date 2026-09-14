"""Ce que l'utilisateur voit : couleurs, styles, alignement, horodatage."""

import threading
import time

import pytest
from rich.console import Console

import ui

MIDI = time.mktime((2026, 9, 14, 12, 34, 56, 0, 0, -1))


def rendu(renderable, width: int = 60) -> str:
    """Le texte tel qu'il atterrit dans le terminal, en passant par emit()."""
    console = Console(width=width, markup=False, highlight=False)
    with console.capture() as capture:
        original, ui.console = ui.console, console
        try:
            ui.emit(renderable)
        finally:
            ui.console = original
    return capture.get()


def styles(text) -> list[str]:
    return [str(span.style) for span in text.spans]


def test_la_couleur_dun_pseudo_est_stable():
    """crc32 et non hash() : hash() est salé, la couleur changerait à chaque run."""
    assert ui.user_style("alice") == ui.user_style("alice")


def test_la_couleur_ignore_la_casse():
    assert ui.user_style("alice") == ui.user_style("ALICE")


def test_des_pseudos_différents_se_distinguent():
    couleurs = {ui.user_style(nom) for nom in ("alice", "bob", "carol", "dave")}
    assert len(couleurs) > 1


def test_un_message_porte_son_horodatage():
    line = ui.chat_line("alice", "salut", when=MIDI)
    assert line.plain.startswith("12:34:56")


def test_un_message_système_porte_son_horodatage():
    line = ui.system_line("bob a rejoint le chat", "italic green", when=MIDI)
    assert line.plain.startswith("12:34:56")


def test_le_pseudo_dun_autre_a_sa_couleur():
    line = ui.chat_line("alice", "salut")
    assert f"bold {ui.user_style('alice')}" in styles(line)


def test_mes_messages_ont_un_style_distinct():
    mien = ui.chat_line("alice", "salut", mine=True)
    autre = ui.chat_line("alice", "salut")
    assert ui.OWN_STYLE in styles(mien)
    assert ui.OWN_STYLE not in styles(autre)


def test_mes_messages_sont_alignés_à_droite():
    assert ui.chat_line("alice", "salut", mine=True).justify == "right"
    assert ui.chat_line("alice", "salut").justify is None


def test_lalignement_est_visible_au_rendu():
    ligne = rendu(ui.chat_line("alice", "salut", mine=True, when=MIDI)).rstrip("\n")
    assert ligne.startswith(" ")
    assert ligne.rstrip().endswith("12:34:56")


def test_le_message_dun_autre_ne_traîne_pas_despaces():
    """justify="left" ferait compléter la ligne jusqu'au bord de l'écran."""
    ligne = rendu(ui.chat_line("alice", "salut", when=MIDI)).rstrip("\n")
    assert ligne == ligne.rstrip()


def test_un_message_système_est_en_italique():
    line = ui.system_line("bob a rejoint le chat", "italic green")
    assert "italic green" in styles(line)


def test_le_texte_dun_message_est_affiché_tel_quel():
    """markup=False : un message qui contient des crochets n'est pas une balise."""
    assert "[bold]coucou[/]" in rendu(ui.chat_line("alice", "[bold]coucou[/]"))


def test_le_panneau_daide_liste_les_commandes():
    sortie = rendu(ui.help_panel("Commandes", [("/help", "affiche cette aide")], "fin"))
    assert "/help" in sortie
    assert "affiche cette aide" in sortie
    assert "Commandes" in sortie


def test_linvite_de_saisie_est_visible():
    assert ui.PROMPT.strip()
    assert ui.PROMPT in ui.RAW_PROMPT


def test_linvite_cache_ses_codes_couleur_à_readline():
    """Sans \\001/\\002, readline compte les codes ANSI comme des colonnes."""
    assert ui.READLINE_PROMPT.count("\001") == ui.READLINE_PROMPT.count("\002") == 2
    assert "\001" not in ui.RAW_PROMPT


def test_les_marqueurs_readline_ne_fuient_pas_hors_dun_terminal(monkeypatch):
    """readline ne les masque que sur un tty ; ailleurs ils s'afficheraient."""
    monkeypatch.setattr(ui, "readline_active", lambda: False)
    assert "\001" not in ui.input_prompt()


def test_linvite_reste_colorée_dans_un_terminal(monkeypatch):
    monkeypatch.setattr(ui, "readline_active", lambda: True)
    assert ui.input_prompt() == ui.READLINE_PROMPT


def test_le_redessin_est_désactivé_hors_dun_terminal(monkeypatch, capsys):
    """Sans readline il n'y a pas de tampon de saisie à redessiner."""
    monkeypatch.setattr(ui, "readline_active", lambda: False)
    monkeypatch.setattr(ui, "_prompt_shown", True)
    monkeypatch.setattr(ui, "console", Console(quiet=True))
    ui.emit(ui.chat_line("alice", "salut"))
    assert capsys.readouterr().out == ""


def test_emit_écrit_sur_la_console(monkeypatch):
    console = Console(width=60, markup=False, highlight=False)
    monkeypatch.setattr(ui, "console", console)
    with console.capture() as capture:
        ui.emit(ui.chat_line("alice", "salut", when=MIDI))
    assert "alice" in capture.get()


def test_emit_sérialise_les_threads(monkeypatch):
    """Le thread réseau et le thread clavier écrivent dans la même sortie."""
    en_cours = []
    chevauchements = []

    class ConsoleLente:
        def print(self, _renderable, **_options):
            en_cours.append(1)
            if len(en_cours) > 1:
                chevauchements.append(1)
            time.sleep(0.01)
            en_cours.pop()

    monkeypatch.setattr(ui, "console", ConsoleLente())
    threads = [
        threading.Thread(target=ui.emit, args=(ui.chat_line("alice", str(i)),))
        for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert chevauchements == []


@pytest.mark.parametrize("saisie", ["bonjour", "  "])
def test_read_line_baisse_le_drapeau_même_en_cas_derreur(monkeypatch, saisie):
    monkeypatch.setattr("builtins.input", lambda _prompt: saisie)
    ui.read_line()
    assert ui._prompt_shown is False


def test_read_line_baisse_le_drapeau_sur_interruption(monkeypatch):
    def interrompt(_prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrompt)
    with pytest.raises(KeyboardInterrupt):
        ui.read_line()
    assert ui._prompt_shown is False
