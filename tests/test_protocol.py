"""Ce que `decode()` garantit au reste du code."""

import pytest

from protocol import COMMAND, InvalidMessage, command_message, decode, encode


def test_aller_retour_encode_decode():
    message = command_message("nick", "alice")
    assert decode(encode(message)) == message


def test_args_absent_devient_liste_vide():
    """La promesse du README : le code qui traite une commande ne vérifie rien."""
    message = decode('{"type":"command","payload":{"name":"users"}}')
    assert message["payload"]["args"] == []


@pytest.mark.parametrize(
    "payload",
    [
        '{"name":"nick","args":"alice"}',
        '{"name":"nick","args":[42]}',
        '{"name":"nick","args":[null]}',
        '{"name":"nick","args":{"0":"alice"}}',
    ],
)
def test_args_mal_typé_est_refusé(payload):
    with pytest.raises(InvalidMessage):
        decode(f'{{"type":"command","payload":{payload}}}')


def test_nom_de_commande_obligatoire():
    with pytest.raises(InvalidMessage):
        decode('{"type":"command","payload":{"args":["alice"]}}')


def test_type_inconnu_est_refusé():
    with pytest.raises(InvalidMessage):
        decode('{"type":"dance","payload":{"text":"hop"}}')


def test_json_illisible_est_refusé():
    with pytest.raises(InvalidMessage):
        decode("{ceci n'est pas du json")


def test_username_non_textuel_dans_un_chat_est_refusé():
    with pytest.raises(InvalidMessage):
        decode('{"type":"chat","payload":{"text":"salut","username":42}}')


def test_args_valide_est_conservé():
    message = decode('{"type":"command","payload":{"name":"nick","args":["a","b"]}}')
    assert message["type"] == COMMAND
    assert message["payload"]["args"] == ["a", "b"]
