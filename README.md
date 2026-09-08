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
2. [Les trois fichiers](#2-les-trois-fichiers)
3. [Le protocole](#3-le-protocole)
4. [Pourquoi `LineReader` existe](#4-pourquoi-linereader-existe)
5. [Le serveur](#5-le-serveur)
6. [Le client](#6-le-client)
7. [La concurrence, en détail](#7-la-concurrence-en-détail)
8. [Les pièges rencontrés](#8-les-pièges-rencontrés-et-leurs-corrections)
9. [Paramètres](#9-paramètres)
10. [Tester](#10-tester)
11. [Par où commencer la lecture](#11-par-où-commencer-la-lecture)

---

## 1. Démarrage

```fish
uv sync                                   # installe rich
uv run python src/server.py -p 12345      # serveur
uv run python src/client.py localhost 12345
```

Le client demande un pseudo, puis tout ce que vous tapez part vers les autres.
`Ctrl-C` ou `Ctrl-D` pour quitter.

---

## 2. Les trois fichiers

```
src/
├── protocol.py   ce qui est commun aux deux bouts : format des messages, lecture du flux
├── server.py     accepte les connexions, tient le registre des pseudos, diffuse
└── client.py     demande le pseudo, envoie ce qu'on tape, affiche ce qui arrive
```

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
| `notice` | information, par exemple la fermeture pour inactivité | |

`text` est toujours prêt à afficher ; `event` existe pour que le code décide
(couleur, fin de la poignée de main) **sans avoir à lire le français**.

### Les commandes

Une seule pour l'instant, celle de la poignée de main ; les commandes tapées
par l'utilisateur (`/quit`, `/list`…) viendront s'ajouter ici.

```json
{"type": "command", "payload": {"name": "nick", "args": ["alice"]}}
```

`args` est toujours une liste de chaînes, même vide : le code qui traite une
commande n'a jamais à en vérifier le type.

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
envoyer ?* (les clés) et *quel pseudo est déjà pris ?* (les valeurs).

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

Les pseudos sont comparés **sans tenir compte de la casse** : `alice` et
`ALICE` ne peuvent pas coexister, car deux pseudos qui se lisent pareil ne
permettraient pas d'identifier qui parle.

---

## 10. Tester

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

## 11. Par où commencer la lecture

Dans cet ordre, chaque fichier s'appuie sur le précédent :

1. **`protocol.py`** — le format des messages, puis `LineReader`. Une fois
   compris que TCP ne découpe pas les messages, le reste coule de source.
2. **`server.py`, `handle_client()`** — la vie d'un client de bout en bout ;
   les autres fonctions du serveur ne sont que ses étapes détaillées.
3. **`server.py`, `broadcast()` et `claim_username()`** — les deux endroits où
   les threads se rencontrent, donc les deux endroits où le verrou compte.
4. **`client.py`, `main()`** — connexion, pseudo, puis les deux threads.

La question à se poser devant chaque ligne du serveur : *combien de threads
peuvent exécuter ceci en même temps, et sur quelles données ?*
