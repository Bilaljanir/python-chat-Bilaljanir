# python-chat

Un chat en temps réel dans le terminal, fait en Python avec des sockets TCP.
Il y a deux programmes : un **serveur** qui accepte les connexions et relaie
les messages, et un **client** qui se connecte au serveur pour discuter.
Tous les clients connectés sont dans le même salon. L'affichage utilise
[Rich](https://rich.readthedocs.io/) pour les couleurs et la mise en forme.

## Fonctionnalités

- plusieurs clients connectés en même temps, chacun voit les messages des autres
- un pseudo unique par personne (sans tenir compte des majuscules)
- des commandes : `/help`, `/users`, `/nick`, `/msg`, `/quit`
- des messages privés entre deux utilisateurs
- les 20 derniers messages publics affichés quand on rejoint le chat
- un tableau de bord côté serveur et un journal dans `server.log`
- les déconnexions et les erreurs sont gérées sans planter : client coupé
  brutalement, client inactif, message invalide, serveur arrêté…

## Prérequis

- Python 3.12 ou plus
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
git clone https://github.com/Bilaljanir/python-chat-Bilaljanir.git
cd python-chat-Bilaljanir
uv sync
```

`uv sync` crée l'environnement virtuel (`.venv`) et installe les dépendances
déclarées dans `pyproject.toml`.

## Utilisation

### Lancer le serveur

```bash
uv run python src/server.py
```

Le serveur écoute par défaut sur le port `12345`. Options :

| Option | Défaut | Rôle |
|---|---|---|
| `-p`, `--port` | `12345` | port d'écoute |
| `-m`, `--max-clients` | `50` | nombre maximum de clients connectés |
| `-t`, `--idle-timeout` | `300` | secondes sans message avant de déconnecter un client |
| `-l`, `--log-file` | `server.log` | fichier du journal |
| `--no-dashboard` | | affiche le journal au lieu du tableau de bord |

`Ctrl-C` arrête le serveur proprement : les clients sont prévenus avant la
fermeture.

### Lancer un client

Dans un autre terminal :

```bash
uv run python src/client.py localhost 12345
```

Sans arguments, le client se connecte à `localhost:12345`. Il demande d'abord
un pseudo, puis tout ce qu'on tape est envoyé aux autres.

### Commandes

| Commande | Effet |
|---|---|
| `/help` | affiche l'aide |
| `/users` | liste les utilisateurs connectés |
| `/nick <pseudo>` | change de pseudo |
| `/msg <pseudo> <message>` | envoie un message privé |
| `/quit` | quitte le chat (`Ctrl-C` et `Ctrl-D` marchent aussi) |

## Comment ça marche

Chaque message est un objet JSON sur une ligne, terminé par `\n` :

```json
{"type": "chat", "payload": {"username": "alice", "text": "salut"}}
```

Le champ `type` dit comment lire le `payload` : `chat`, `private`, `system`,
`command` ou `history`. C'est le serveur qui ajoute le pseudo de l'auteur, un
client ne peut donc pas parler sous le nom d'un autre.

Côté serveur, chaque client a son propre thread qui lit ses messages. Les
envois vers un client passent par une file d'attente vidée par un thread à
part : un client qui ne lit plus ne bloque pas le chat pour les autres.

## Structure

```
src/
├── server.py     le serveur : connexions, pseudos, relais des messages
├── client.py     le client : saisie, commandes, réception
├── protocol.py   le format des messages, commun au client et au serveur
├── registry.py   la liste des clients connectés et leurs pseudos
├── history.py    les derniers messages publics
├── ui.py         l'affichage du client avec Rich
└── admin.py      le journal et le tableau de bord du serveur
```

## Qualité du code

Le code est formaté et vérifié avec [Ruff](https://docs.astral.sh/ruff/) :

```bash
uv run ruff format src
uv run ruff check src
```
