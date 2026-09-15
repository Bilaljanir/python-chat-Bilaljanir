# python-chat

Un chat de groupe en TCP : un serveur, plusieurs clients dans un terminal.
Les deux bouts parlent un protocole **JSON délimité par des sauts de ligne**
(voir [Le protocole](#3-le-protocole)). Écrit avec la bibliothèque standard
uniquement (`socket`, `threading`, `json`), plus
[rich](https://rich.readthedocs.io/) pour l'affichage.

```
$ uv run python src/server.py            # terminal 1
$ uv run python src/client.py            # terminal 2  -> pseudo : alice
$ uv run python src/client.py            # terminal 3  -> pseudo : bob
```

---

## Sommaire

1. [Démarrage](#1-démarrage)
2. [Les fichiers](#2-les-fichiers)
3. [Le protocole](#3-le-protocole)
4. [Pourquoi `LineReader` existe](#4-pourquoi-linereader-existe)
5. [Le serveur](#5-le-serveur)
6. [Le client](#6-le-client)
7. [La concurrence, en détail](#7-la-concurrence-en-détail)
8. [Les pièges rencontrés](#8-les-pièges-rencontrés-et-leurs-corrections)
9. [Paramètres](#9-paramètres)
10. [Administration et journal](#10-administration-et-journal)
11. [Tester](#11-tester)
12. [Par où commencer la lecture](#12-par-où-commencer-la-lecture)

---

## 1. Démarrage

```fish
uv sync                                   # installe rich
uv run python src/server.py -p 12345      # serveur
uv run python src/client.py localhost 12345
```

Le client demande un pseudo, puis tout ce que vous tapez part vers les autres.
Les lignes commençant par `/` sont des commandes : `/help`, `/users`,
`/nick <pseudo>`, `/quit`. `Ctrl-C` ou `Ctrl-D` quittent aussi.

---

## 2. Les fichiers

```
src/
├── protocol.py   ce qui est commun aux deux bouts : format des messages, lecture du flux
├── server.py     accepte les connexions, tient le registre des pseudos, diffuse
├── client.py     demande le pseudo, envoie ce qu'on tape, affiche ce qui arrive
├── ui.py         la présentation côté client : couleurs, panneaux, invite redessinée
└── admin.py      la présentation côté serveur : journal, tableau de bord, niveaux
```

Les trois premiers portent la logique ; les deux derniers ne portent que
l'affichage. Le partage est le même des deux côtés : **un module décide *quoi*
dire, un autre décide *comment* ça se voit.** `server.py` ne sait pas dessiner,
il journalise ; `admin.py` ne sait rien du registre, il reçoit un état déjà figé.

La règle de partage est simple : **tout ce que le serveur et le client doivent
comprendre de la même façon vit dans `protocol.py`.** Si les deux côtés
interprétaient différemment la fin d'une ligne ou le nom d'un champ, ils ne se
comprendraient plus. C'est pourquoi personne n'appelle `json.dumps` ailleurs :
les messages se construisent avec `chat_message()`, `system_message()` et
`command_message()`, et se lisent avec `decode()`.

---

## 3. Le protocole

**Un message = un objet JSON sur une ligne, terminé par `\n`, encodé en
UTF-8** (*newline-delimited JSON*). Chaque objet a exactement deux champs :

```json
{"type": "chat", "payload": {"username": "alice", "text": "salut bob"}}
```

- **`type`** dit comment lire la suite ;
- **`payload`** porte les données propres à ce type.

Pourquoi pas du texte brut préfixé (`OK alice`) ? Parce que dès qu'un message
porte deux informations — l'auteur *et* le texte, l'événement *et* son
libellé — il faut inventer un séparateur, puis se demander ce qui se passe
quand ce séparateur apparaît dans le texte. JSON tranche la question une fois
pour toutes, et `json` est dans la bibliothèque standard. Le `\n` reste le
délimiteur : il ne peut pas apparaître dans une ligne JSON, où il s'écrit
`\\n`.

### Les trois types

| `type` | Sens | Émis par | Champs du `payload` |
|---|---|---|---|
| `chat` | un message de discussion | client → serveur → les autres | `text`, plus `username` quand le serveur rediffuse |
| `system` | une notification du serveur | serveur | `event`, `text`, parfois `username` |
| `command` | une demande du client | client | `name`, `args` (liste de chaînes) |

Un `chat` venant du client ne porte que `text` : **c'est le serveur qui
attache l'auteur**, pris dans son registre. Un `username` glissé par le client
dans son payload est ignoré — sans quoi n'importe qui parlerait sous le nom
d'un autre.

### Les événements `system`

| `event` | Quand | Champ en plus |
|---|---|---|
| `ask_username` | le serveur réclame un pseudo | |
| `error` | pseudo refusé, commande inconnue, message rejeté | |
| `welcome` | pseudo accepté | `username` |
| `join` | un client rejoint le chat | `username` |
| `leave` | un client quitte le chat | `username` |
| `rename` | un client a changé de pseudo | `username` (l'ancien), `new_username` |
| `user_list` | réponse à `/users` | `users` (liste de pseudos) |
| `notice` | information, par exemple la fermeture pour inactivité | |

`text` est toujours prêt à afficher ; `event` existe pour que le code décide
(couleur, fin de la poignée de main) **sans avoir à lire le français**.

### Les commandes

```json
{"type": "command", "payload": {"name": "nick", "args": ["alice"]}}
```

`args` est toujours une liste de chaînes, même vide : le code qui traite une
commande n'a jamais à en vérifier le type.

| `name` | Envoyée quand | Réponse du serveur |
|---|---|---|
| `nick` | poignée de main, puis `/nick <pseudo>` | `welcome` ou `rename`, ou `error` si le pseudo est pris |
| `users` | `/users` | `user_list` |
| `quit` | `/quit` | aucune : le serveur termine la session et diffuse `leave` |

Une commande inconnue reçoit un `system` / `error` — le serveur ne suppose
jamais que son pair est le client de ce dépôt.

### Un échange complet

```
client                                              serveur
  |                                                    |
  |--------------- connexion TCP --------------------->|  thread créé
  |<-- {"type":"system","payload":{"event":"ask_username","text":"Choisissez un pseudo"}}
  |--> {"type":"command","payload":{"name":"nick","args":["alice"]}}
  |                                                    |  claim_username() -> libre
  |<-- {"type":"system","payload":{"event":"welcome","username":"alice",...}}
  |                                                    |  broadcast -> les autres
  |                                                    |    system / join / alice
  |--> {"type":"chat","payload":{"text":"salut bob"}}
  |                                                    |  broadcast -> les autres
  |                                                    |    chat / alice / "salut bob"
  |<-- {"type":"chat","payload":{"username":"bob","text":"salut alice"}}
  |                                                    |
  |--------------- Ctrl-C ---------------------------->|  recv rend b"" -> fin du thread
                                                       |  broadcast -> les autres
                                                       |    system / leave / alice
```

Si le pseudo est pris, le serveur **redemande sur la même connexion** au lieu
de couper — le client n'a pas à se reconnecter. Au bout de 3 refus, la
connexion est fermée.

### Un message invalide est journalisé, jamais fatal

`decode()` refuse une ligne dès qu'elle ne tient pas debout :

| Ligne reçue | Refus |
|---|---|
| `pas du json` | JSON illisible |
| `[1,2,3]` | le message n'est pas un objet JSON |
| `{"type":"chat"}` | `payload` absent ou n'est pas un objet |
| `{"type":"zorglub","payload":{}}` | type inconnu |
| `{"type":"chat","payload":{}}` | champ `text` absent ou non textuel |
| `{"type":"command","payload":{"name":"nick","args":[42]}}` | `args` n'est pas une liste de chaînes |

`iter_messages()` attrape ce refus, le signale et **passe à la ligne
suivante** : le serveur l'écrit dans son journal, le client l'affiche en
grisé, et la connexion continue. C'est le seul endroit où ce « journaliser et
ignorer » est écrit, pour les deux bouts.

En échange, `decode()` garantit à ses appelants que `message["type"]` est l'un
des trois types connus, que `message["payload"]` est un dictionnaire et que
les champs obligatoires de ce type sont présents et textuels. Sans ce
contrat, chaque `payload["text"]` du serveur et du client serait un `KeyError`
déclenchable à distance.

---

## 4. Pourquoi `LineReader` existe

C'est le point le moins intuitif du projet, et celui qui cause le plus de bugs
quand on ne le traite pas.

> **TCP est un flux d'octets, pas un flux de messages.**
> Il garantit l'ordre et l'intégrité, mais **pas** les frontières d'envoi.

Trois conséquences, toutes vécues dans ce projet :

**a) Un message peut arriver en morceaux.** `sendall("bonjour")` côté client
peut donner deux `recv` côté serveur : `"bon"` puis `"jour"`. Sans tampon, les
autres verraient `[alice]: bon` puis `[alice]: jour`.

**b) Plusieurs messages peuvent arriver ensemble.** Deux envois rapides
peuvent se retrouver dans un seul `recv` : `"salut\nça va\n"`. Il faut donc
découper, pas juste afficher le bloc.

**c) Un caractère UTF-8 peut être coupé en deux.** `é` fait deux octets ; si la
coupure tombe entre les deux, un `bytes.decode()` naïf lève une exception ou
produit un caractère de remplacement. D'où le **décodeur incrémental**
(`codecs.getincrementaldecoder`), qui garde l'octet orphelin pour le bloc
suivant.

`LineReader` règle les trois d'un coup :

```python
reader = LineReader(sock, idle_timeout)
messages = iter_messages(reader, on_invalid)   # lignes -> messages décodés
for message in messages:    # un tour = un message complet et conforme
    ...
```

Les deux étages sont séparés exprès : `LineReader` ne connaît que les octets
et les `\n`, `iter_messages` ne connaît que le JSON. Le premier n'a pas eu à
changer quand le protocole est passé du texte brut au JSON.

### Comment il s'y prend

| Méthode | Rôle |
|---|---|
| `lines()` | l'itérateur public — toujours **le même objet**, voir ci-dessous |
| `_drain_buffer()` | rend les lignes complètes déjà dans le tampon |
| `_fill_buffer()` | un `recv`, l'ajoute au tampon ; `False` quand le flux se termine |
| `_arm_timeout()` | arme le délai avant le prochain `recv` |
| `_reject_if_too_long()` | la limite `MAX_MESSAGE_LEN`, en un seul endroit |
| `iter_messages()` | décode chaque ligne ; signale et saute les invalides |

Deux détails qui ont leur importance :

- **`lines()` est mémoïsé.** La poignée de main consomme quelques lignes, puis
  la boucle de chat consomme le reste. Si chaque appel créait un nouveau
  générateur, l'état serait dispersé et des lignes se perdraient.
- **La limite de taille s'applique aussi au reliquat non terminé.** Sinon, un
  pair qui n'envoie jamais de `\n` ferait grossir le tampon sans limite : une
  fuite de mémoire déclenchable à distance.

---

## 5. Le serveur

### La boucle d'accueil

```python
def serve(server_socket, max_clients, idle_timeout):
    sem = threading.Semaphore(max_clients)
    while True:
        sem.acquire()                    # bloque si le serveur est plein
        conn, address = server_socket.accept()
        threading.Thread(target=handle_client, args=(...), daemon=True).start()
```

**Un thread par client.** C'est le modèle le plus lisible pour un chat : chaque
thread passe l'essentiel de son temps bloqué dans `recv`, ce qui ne coûte pas
de CPU. Le **sémaphore** borne le nombre de threads : au 51ᵉ client, l'`accept`
attend qu'une place se libère au lieu de laisser les threads se multiplier.
Chaque thread rend son permis (`sem.release()`) en partant, **quel que soit le
chemin de sortie** — c'est pour ça qu'il est placé tout à la fin de
`handle_client`, hors du `try`.

### Le registre

```python
clients: dict[socket.socket, str] = {}   # socket -> pseudo
clients_lock = threading.Lock()
```

Un dictionnaire, parce qu'il répond aux deux questions du chat : *à qui dois-je
envoyer ?* (les clés) et *quel pseudo est déjà pris ?* (les valeurs). Il répond
aussi aux deux commandes ajoutées ensuite : `/users` lit ses valeurs, `/nick`
en remplace une.

Depuis `/nick`, **le pseudo n'est plus une variable de `handle_client`** : il
est relu dans le registre à chaque message relayé (`current_username(conn)`).
Une variable locale aurait continué à attribuer les messages à l'ancien nom, et
le `leave` de fin de session aurait annoncé le départ de quelqu'un qui n'existe
plus.

`claim_username` sert les deux cas — poignée de main et renommage — grâce à une
condition unique :

```python
if any(sock is not conn and taken.casefold() == name.casefold()
       for sock, taken in clients.items()):
```

Le `sock is not conn` est ce qui permet à `alice` de devenir `ALICE` : on
ignore sa propre entrée dans le test d'unicité, sans quoi tout renommage
buterait sur son propre pseudo.

### La diffusion

```python
def broadcast(message, sender=None):
    data = f"{encode(message)}\n".encode()   # sérialisé une fois pour tous
    with clients_lock:
        targets = [sock for sock in clients if sock is not sender]   # copie
    unreachable = [sock for sock in targets if not try_send(sock, data)]
    for sock in unreachable:
        drop_client(sock)
```

Trois décisions dans ces six lignes :

1. **`sock is not sender`** : c'est ce qui implémente « l'auteur ne reçoit pas
   son propre message ». Le message est aussi **sérialisé une seule fois**,
   avant la boucle : le JSON envoyé est identique pour tous les destinataires.
2. **Le verrou n'est tenu que pour la copie**, pas pendant les envois. Un
   `sendall` peut bloquer si le tampon d'un client est plein ; le tenir sous
   verrou figerait tout le chat à cause d'un seul client lent.
3. **Un socket qui refuse l'envoi est purgé** — et `drop_client` fait un
   `shutdown`, pas seulement un retrait du registre. Sans ce `shutdown`, le
   client resterait connecté, capable d'envoyer, mais ne recevrait plus rien.
   `shutdown` plutôt que `close` : le `recv` du thread propriétaire rend `b""`
   et ce thread sort par son chemin normal, en libérant son permis. Un `close`
   depuis un autre thread lui ferait lever `OSError: Bad file descriptor` avant
   son `sem.release()`, et le permis serait perdu pour de bon.

### La vie d'un thread client

```
handle_client()
   │
   ├─ negotiate_username()      jusqu'à 3 essais ─┐ échec → "Rejected", release
   │                                              ↓ succès
   ├─ broadcast(system_message(JOIN, "x a rejoint le chat"))
   ├─ relay_messages()          boucle jusqu'à déconnexion ou silence
   │
   └─ retrait du registre
      broadcast(system_message(LEAVE, "x a quitté le chat"))
      sem.release()
```

---

## 6. Le client

Deux threads, parce qu'il faut **attendre le clavier et le réseau en même
temps** :

| Thread | Rôle | Fonction |
|---|---|---|
| principal | `input()` → `chat_message()` → `send_message()` | `send_user_input()` |
| secondaire | `recv` → `decode()` → affichage | `receive_messages()` |

L'affichage est le seul endroit du client qui traduit un message en texte :
`display()` met la couleur d'après `payload["event"]` et écrit
`[alice]: salut` à partir de `username` et `text`. Le format d'affichage n'est
donc plus imposé par le réseau — le serveur envoie des données, le client
choisit comment les montrer.

### Les commandes

Tout ce qui commence par `/` est **intercepté avant l'envoi** : une commande
n'est jamais diffusée comme un message ordinaire.

| Tapé | Traité où | Effet |
|---|---|---|
| `/help` | entièrement sur le client | affiche la liste des commandes |
| `/users` | envoi de `command`/`users` | le serveur répond `user_list`, affiché tel quel |
| `/nick <pseudo>` | envoi de `command`/`nick` | pseudo changé si libre, les autres reçoivent `rename` |
| `/quit` | envoi de `command`/`quit`, puis sortie | le serveur clôt la session et diffuse `leave` |
| `/autre` | entièrement sur le client | `Commande inconnue : /autre — tapez /help` |

```python
name, _, rest = text[1:].partition(" ")
return name.casefold(), rest.split()
```

Deux conséquences de ce découpage :

- **Le client connaît la liste des commandes**, donc une faute de frappe est
  signalée immédiatement, sans aller-retour réseau ni ligne inutile envoyée au
  serveur. `/nick` sans argument affiche son usage au lieu d'envoyer une
  commande vide.
- **Le serveur ne fait confiance à personne** : il revalide le pseudo et
  répond `error` à une commande qu'il ne connaît pas. `nc` peut envoyer
  n'importe quoi, le client n'est pas le seul garde-fou.

Le pseudo affiché après un `/nick` vient du serveur, jamais d'une variable
locale : c'est le registre qui fait foi, et les messages suivants sont
attribués au nouveau nom sans que le client ait à s'en souvenir.

Deux subtilités valent l'explication :

**`markup=False` à l'affichage.** rich interprète les crochets comme des
balises de style : `console.print("[alice]: salut")` affiche `: salut` — le
pseudo disparaît, avalé comme une balise inconnue. Toute ligne venant du réseau
est donc affichée avec `markup=False`.

**Le réveil du thread principal.** Quand le serveur ferme la connexion, le
thread de réception le voit tout de suite, mais le thread principal reste
bloqué dans `input()`. Or `input()` attend dans un appel système :
`_thread.interrupt_main()` n'agirait qu'à la frappe suivante. Seul un vrai
signal interrompt l'attente :

```python
os.kill(os.getpid(), signal.SIGINT)     # -> KeyboardInterrupt dans main()
```

Sans ça, le client reste figé et il faut taper une touche pour qu'il rende la
main.

---

## 7. La concurrence, en détail

Tout ce qui suit est partagé entre threads, donc protégé par `clients_lock` :

| Ce qui est protégé | Où | Pourquoi |
|---|---|---|
| lire la liste des destinataires | `broadcast` | un dict ne doit pas changer de taille pendant qu'on l'itère |
| vérifier l'unicité **et** insérer | `claim_username` | voir ci-dessous |
| retirer un client | `drop_client`, fin de `handle_client` | même raison |

### Le point crucial : unicité et insertion sous le même verrou

```python
with clients_lock:
    if any(taken.casefold() == name.casefold() for taken in clients.values()):
        return "Ce pseudo est déjà utilisé, choisissez-en un autre."
    clients[conn] = name          # dans le MÊME bloc with
```

Si la vérification et l'insertion étaient dans deux blocs séparés, deux clients
demandant `alice` en même temps pourraient tous deux passer la vérification
avant que l'un des deux n'insère — et le chat aurait deux `alice`. La règle
générale : **une décision prise à partir d'un état partagé doit être appliquée
sans relâcher le verrou entre-temps.**

Un test dédié lance 20 threads réclamant le même pseudo simultanément et vérifie
qu'un seul reçoit un message système `welcome`.

### Ce qui n'a pas besoin de verrou

Le tampon d'un `LineReader` appartient à un seul thread — celui qui sert ce
client. Pas de partage, pas de verrou.

---

## 8. Les pièges rencontrés (et leurs corrections)

Ces bugs ont réellement été trouvés pendant le développement ; ils expliquent
la forme actuelle du code.

| Symptôme | Cause | Correction |
|---|---|---|
| un message long arrivait en plusieurs morceaux préfixés | pas de tampon : chaque `recv` était traité comme un message | tampon + découpe sur `\n` |
| accents cassés | `decode()` sur un caractère coupé en deux | décodeur UTF-8 incrémental |
| le pseudo disparaissait à l'écran | rich lisait `[alice]` comme une balise | `markup=False` |
| « Server closed the connection » sans raison | timeout d'inactivité, jamais annoncé | le serveur explique avant de fermer |
| `timed_out` toujours faux | le `TimeoutError` de `recv` était avalé par le `except` | il est intercepté dans `_fill_buffer` |
| le client restait figé après la fermeture | `input()` bloque au niveau système | SIGINT auto-envoyé |
| un client purgé restait connecté sans rien recevoir | retrait du registre sans `shutdown` | `drop_client` fait les deux |
| `--idle-timeout inf` gelait une place | valeur non validée, exception avant `sem.release()` | validée par argparse |
| une ligne trop longue passait quand même | la limite ne visait que le reliquat | contrôle sur la ligne extraite |

### Deux pièges propres au passage au JSON

- **Lire un `payload` sans le valider.** Un `payload["text"]` sur un message
  incomplet est un `KeyError` — donc un thread client qui meurt — déclenchable
  par n'importe quel pair. D'où la validation centralisée dans `decode()`.
- **L'enveloppe compte dans la longueur de la ligne.** Un texte de 4000
  caractères tient sous `MAX_MESSAGE_LEN`, mais pas une fois entouré de son
  JSON : la ligne serait rejetée et la connexion fermée, sans explication.
  D'où `MAX_TEXT_LEN`, nettement plus bas, vérifié par le client avant
  l'envoi et par le serveur à la réception.

---

## 9. Paramètres

### Serveur

| Option | Défaut | Effet |
|---|---|---|
| `-p, --port` | 12345 | port d'écoute |
| `-m, --max-clients` | 50 | connexions simultanées ; au-delà, l'`accept` attend |
| `-t, --idle-timeout` | 300 | secondes de silence avant déconnexion |
| `-l, --log-file` | `server.log` | fichier où le journal est ajouté |
| `--no-dashboard` | — | journal déroulant au lieu du tableau de bord |

`--max-clients` et `--idle-timeout` refusent zéro, les valeurs négatives et
`inf` : une valeur absurde ne doit pas se transformer en panne à l'exécution.

### Constantes

| Nom | Valeur | Fichier | Rôle |
|---|---|---|---|
| `MAX_MESSAGE_LEN` | 4096 | `protocol.py` | longueur maximale d'une ligne JSON |
| `MAX_TEXT_LEN` | 1024 | `protocol.py` | longueur maximale d'un texte de chat |
| `RECV_SIZE` | 1024 | `protocol.py` | taille d'un bloc lu |
| `MAX_NAME_ATTEMPTS` | 3 | `server.py` | essais de pseudo avant fermeture |
| `NAME_PATTERN` | `^[\w.-]{1,24}$` | `server.py` | pseudos acceptés |
| `ACCEPT_TIMEOUT` | 0.5 | `server.py` | secondes avant que la boucle d'accueil relise `stop` |
| `ACTIVITY_LINES` | 12 | `admin.py` | lignes de journal gardées en mémoire |
| `REFRESH_DELAY` | 0.5 | `admin.py` | secondes entre deux redessins |

Les pseudos sont comparés **sans tenir compte de la casse** : `alice` et
`ALICE` ne peuvent pas coexister, car deux pseudos qui se lisent pareil ne
permettraient pas d'identifier qui parle.

---

## 10. Administration et journal

Le serveur tourne sans personne devant lui. Deux besoins en découlent : voir
d'un coup d'œil ce qui se passe *maintenant*, et pouvoir relire *après coup* ce
qui s'est passé. Ce sont deux vues d'une même source — le journal.

### Le serveur journalise, il n'affiche pas

`server.py` n'appelle plus `console.log`. Il appelle `logger.info(...)`, et
c'est `admin.py` qui décide où cela va :

```python
logger.info("Connexion : %s (%s:%s)", username, host, port)
```

Les arguments sont passés à `logging`, pas interpolés dans une f-string : une
ligne de niveau `DEBUG` ne coûte rien tant que ce niveau n'est pas activé.
`setup_logging()` branche trois destinations :

| Destination | Rôle |
|---|---|
| `logging.FileHandler` | `server.log`, en ajout, encodé en UTF-8 |
| `rich.logging.RichHandler` | le terminal, en couleurs |
| `admin.ActivityLog` | un `deque` borné, relu par le tableau de bord |

`ActivityLog` est un handler comme les deux autres : rien n'est journalisé
deux fois, et le tampon ne grandit pas avec la durée de vie du serveur.

`RichHandler` est configuré avec **`markup=False`**. Les pseudos viennent du
réseau : un pseudo écrit `[red]` ne doit pas piloter les couleurs du terminal
de l'administrateur.

### Le tableau de bord

Par défaut, un `rich.live.Live` occupe le bas du terminal et se redessine deux
fois par seconde :

```
╭─ Serveur de chat ────────────────────────────────╮
│  Écoute      0.0.0.0:12345                       │
│  Clients     2 / 50                              │
│  En ligne    00:04:12                            │
│  Connectés   alice, bob                          │
╰──────────────────────────────────────────────────╯
╭─ Activité récente ───────────────────────────────╮
│  09:12:01  INFO      Connexion : alice           │
│  09:12:07  INFO      Connexion : bob             │
│  09:12:19  WARNING   Message invalide de …       │
╰──────────────────────────────────────────────────╯
```

`admin.dashboard()` reçoit une liste de pseudos et une liste de lignes — pas le
registre, pas le handler. Elle ne prend donc aucun verrou et se teste sans
serveur : on lui donne un état, on lit ce qu'elle dessine.

C'est `dashboard_snapshot()`, côté serveur, qui fige cet état à chaque redessin.

Tant que le tableau de bord est affiché, le `RichHandler` du terminal est
remonté à `WARNING`. Ce n'est pas seulement pour éviter de tout écrire deux
fois : le panneau d'activité ne garde que `ACTIVITY_LINES` lignes et les perd
en défilant, alors qu'un `WARNING` imprimé au-dessus du tableau de bord reste
dans l'historique du terminal. Les deux vues sont donc complémentaires — le
courant passe dans le panneau, ce qui compte s'inscrit dans le défilement.

Le tableau de bord s'efface tout seul quand la sortie n'est pas un terminal
(un `| tee`, un service, pytest) : on retombe alors sur le journal déroulant,
comme avec `--no-dashboard`.

### L'arrêt : prévenir avant de fermer

Couper le serveur sans rien dire laisse chaque client face à une socket morte.
`shutdown_clients()` fait l'inverse : il vide le registre, envoie un `notice` à
tout le monde, puis ferme.

```
$ uv run python src/server.py
^C
# côté client : « Le serveur s'arrête, à bientôt. »
#               « Connexion fermée par le serveur »
```

Vider le registre **d'abord** n'est pas cosmétique : un thread client qui était
en train de relayer un message voit son pseudo disparaître et s'arrête de
lui-même (`relay_messages` le vérifie à chaque tour), au lieu de diffuser dans
un serveur en train de fermer. Sans cela, chaque thread annoncerait en partant
un `leave` à tous les autres — *n* départs diffusés *n* fois, dans un serveur
qui ferme.

Tout part d'un seul drapeau, `stop`, posé par `install_stop_handlers()` :

```python
for signum in (signal.SIGINT, signal.SIGTERM):
    signal.signal(signum, request_stop)
```

Attendre un `KeyboardInterrupt` n'aurait couvert que `SIGINT`. Un `docker stop`
ou un `systemctl stop` envoie `SIGTERM` : sans gestionnaire, le processus meurt
sur-le-champ et personne n'est prévenu — précisément le cas où l'arrêt propre
sert le plus. Le gestionnaire se retire dès le premier signal, si bien qu'un
second `Ctrl-C` retombe sur le comportement par défaut et tue le serveur, si
l'arrêt devait s'éterniser.

Un gestionnaire de signal s'exécute toujours dans le thread principal. C'est
pourquoi `main()` a été retourné : `serve()` part dans un thread, et le thread
principal reste disponible — il dessine le tableau de bord, ou bloque sur
`stop.wait()`. Dans les deux cas, poser `stop` le réveille.

`serve()` lit ce même drapeau, mais **en tête de boucle**, pas au fond d'un
`except`. C'est ce que permet le `settimeout(ACCEPT_TIMEOUT)` sur la socket
d'écoute : `accept()` rend la main toutes les demi-secondes, la boucle relit
`stop` et sort d'elle-même. Sans ce délai, il aurait fallu fermer la socket
sous un `accept()` bloqué dans un autre thread, puis deviner, depuis
l'`OSError` qui en sort, s'il s'agissait d'un arrêt ou d'une panne.

---

## 11. Tester

### À la main

Un serveur, deux clients, et on vérifie :

| Action | Attendu |
|---|---|
| bob se connecte | alice voit `bob a rejoint le chat` |
| bob tape `salut` | alice voit `[bob]: salut`, bob ne voit rien |
| un 3ᵉ client tape `alice` | refus, puis nouvelle invite |
| il tape `ALICE` | refus aussi |
| il tape `a b` | `Pseudo invalide : ...` |
| 3 refus d'affilée | connexion fermée |
| `Ctrl-C` sur bob | alice voit `bob a quitté le chat` |
| `Ctrl-C` sur le serveur | les clients voient `Le serveur s'arrête, à bientôt.`, puis la fermeture |
| `kill <pid>` du serveur (`SIGTERM`) | même chose : l'arrêt propre ne dépend pas du clavier |
| après coup | `server.log` contient les connexions, départs et refus |

Puis les commandes :

| Action | Attendu |
|---|---|
| bob tape `/help` | la liste s'affiche chez bob, alice ne voit rien |
| bob tape `/bidule` | `Commande inconnue : /bidule`, rien n'est envoyé |
| bob tape `/users` | `Connectés (2) : alice, bob` |
| bob tape `/nick carol` | bob voit `Vous êtes désormais carol`, alice voit `bob est désormais carol` |
| carol tape `salut` | alice voit `[carol]: salut` (et non `[bob]`) |
| carol tape `/nick alice` | refus : le pseudo est pris |
| carol tape `/nick CAROL` | accepté : changer la casse de son propre pseudo est permis |
| carol tape `/quit` | carol sort, alice voit `CAROL a quitté le chat` |

### Sans client

`nc` suffit pour voir le protocole brut — et pour l'écrire à la main :

```fish
nc localhost 12345
{"type":"system","payload":{"event":"ask_username","text":"Choisissez un pseudo"}}
{"type":"command","payload":{"name":"nick","args":["alice"]}}
{"type":"system","payload":{"event":"welcome","text":"Connecté en tant que alice","username":"alice"}}
{"type":"chat","payload":{"text":"salut"}}
```

(les lignes 1 et 3 viennent du serveur, les lignes 2 et 4 sont tapées.)

C'est aussi le moyen de vérifier le traitement des messages invalides : tapez
`n'importe quoi`, puis `{"type":"chat"}`. Le serveur journalise deux refus,
**garde la connexion ouverte**, et le message suivant passe normalement.

---

## 12. Par où commencer la lecture

Dans cet ordre, chaque fichier s'appuie sur le précédent :

1. **`protocol.py`** — le format des messages, puis `LineReader`. Une fois
   compris que TCP ne découpe pas les messages, le reste coule de source.
2. **`server.py`, `handle_client()`** — la vie d'un client de bout en bout ;
   les autres fonctions du serveur ne sont que ses étapes détaillées.
3. **`server.py`, `broadcast()` et `claim_username()`** — les deux endroits où
   les threads se rencontrent, donc les deux endroits où le verrou compte.
4. **`client.py`, `main()`** — connexion, pseudo, puis les deux threads.
5. **`admin.py`** — pour finir : ce que tout cela donne à voir, et rien d'autre.

La question à se poser devant chaque ligne du serveur : *combien de threads
peuvent exécuter ceci en même temps, et sur quelles données ?*
