---
title: Léa réceptionniste
emoji: 🦷
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: Réceptionniste vocale pour cabinet dentaire
---

# Léa — réceptionniste IA pour cabinet dentaire

Léa répond aux patients, recueille le motif, l'ancienneté du problème, le
niveau de douleur, le nom et le téléphone, propose des créneaux libres,
reformule la date et réserve le rendez-vous. Elle signale les urgences
(gonflement, fièvre, saignement).

- 100 % gratuit, open source et **local** : tourne sur CPU (testé pour
  MacBook Pro 2019 Intel).
- LLM léger via Ollama (`qwen2.5:3b` par défaut), n'importe quelle API
  compatible OpenAI possible.
- Agenda : fichier JSON local (défaut) ou Google Calendar (OAuth).

| Étape | État |
|---|---|
| 1. Agent texte en CLI + outils + agendas + tests | ✅ |
| 2. Voix (Pipecat, SmallWebRTC, Silero VAD, faster-whisper, Piper / Kokoro), appel depuis le téléphone | ✅ |
| 3. Rapide et hébergée gratuitement : Groq + Hugging Face Spaces + Google Agenda | ✅ code prêt |

---

## 1. Prérequis (macOS Intel, zsh)

### Homebrew

```zsh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# Sur Mac Intel, brew s'installe dans /usr/local : rien d'autre à faire.
brew --version
```

### uv (gestion de Python et des dépendances)

```zsh
brew install uv
uv --version
```

### Ollama + modèle 3B

```zsh
brew install ollama
brew services start ollama          # lance le serveur en arrière-plan (port 11434)
ollama pull qwen2.5:3b              # ~1,9 Go, bon en français et en appels d'outils
ollama run qwen2.5:3b "Bonjour"     # test rapide (Ctrl+D pour sortir)
```

Alternative : `ollama pull llama3.2:3b` puis `LLM_MODEL=llama3.2:3b` dans `.env`.
Sur un i7/i9 de 2019, comptez environ 10 secondes par réponse.

Pour arrêter Ollama : `brew services stop ollama`.

#### Sans Homebrew (installation faite sur ce Mac)

```zsh
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv dans ~/.local/bin
curl -L -o ~/Downloads/Ollama-darwin.zip https://ollama.com/download/Ollama-darwin.zip
ditto -x -k ~/Downloads/Ollama-darwin.zip /Applications   # app signée et notarisée
ln -sf /Applications/Ollama.app/Contents/Resources/ollama ~/.local/bin/ollama
open -a Ollama                                            # serveur (icône dans la barre de menus)
ollama pull qwen2.5:3b
```

### Garde-fous pour les petits modèles

Un modèle 3B oublie souvent d'appeler les outils, ou invente un nom ou un
numéro. Le code verrouille donc les étapes critiques :

- **Urgence** : gonflement, fièvre et saignement sont détectés par mots-clés
  et `flag_urgence` est appelé par le code.
- **Réservation** : elle n'a lieu qu'au « oui » du patient qui suit un
  récapitulatif contenant une date et une heure. Le créneau est relu dans
  cette phrase, le téléphone dans les messages du patient, et le nom doit
  avoir été dit par le patient. Un appel spontané du modèle à
  `book_appointment` est refusé avec une consigne de récapituler.
- **Relance** : si Léa annonce « je vais chercher… » ou dit « réservé »
  sans l'avoir fait, elle est relancée (deux fois au plus).

---

## 2. Installation du projet

```zsh
cd ~/tech+ia/assistant-ia
uv python install 3.12
uv sync                       # crée .venv avec Python 3.12 et installe tout
cp .env.example .env          # puis ajustez si besoin (nom du cabinet, horaires…)
```

## 3. Lancer Léa en mode texte

```zsh
uv run lea                    # conversation dans le terminal
uv run lea --debug            # affiche aussi les appels d'outils
uv run lea --model llama3.2:3b
```

Commandes pendant la conversation : `/reset` (nouvel appel), `/quit`.

Les rendez-vous sont écrits dans `data/calendar.json`, les urgences dans
`data/urgences.jsonl`.

## 4. Tests

```zsh
uv run pytest
```

Ils couvrent les dates relatives (« demain », « lundi prochain », « mardi en
huit », « le 12 », « 1er décembre »…), les horaires (8h-17h, créneaux de
30 min, lun-ven), la prise de rendez-vous, les urgences et la boucle de
l'agent (avec un faux LLM : Ollama n'est pas nécessaire pour les tests).

> Convention : « lundi » / « lundi prochain » = le prochain lundi après
> aujourd'hui (dit un lundi : dans 7 jours). Léa reformule toujours la date
> complète avant de réserver, ce qui lève l'ambiguïté à l'oral.

---

## 5. Google Calendar (facultatif)

1. Ouvrez <https://console.cloud.google.com/>, créez un projet (gratuit).
2. **API et services → Bibliothèque** : activez **Google Calendar API**.
3. **API et services → Écran de consentement OAuth** : type *Externe*,
   renseignez un nom, puis ajoutez votre adresse Gmail dans **Utilisateurs
   test**.
4. **API et services → Identifiants → Créer des identifiants → ID client
   OAuth** : type **Application de bureau**. Téléchargez le JSON.
5. Puis :

```zsh
mv ~/Downloads/client_secret_*.json ~/tech+ia/assistant-ia/credentials.json
cd ~/tech+ia/assistant-ia
uv sync --extra google
uv run lea-google-auth        # ouvre le navigateur, crée token.json
```

6. Dans `.env` : `CALENDAR_BACKEND=google` (et éventuellement
   `GOOGLE_CALENDAR_ID=<id d'un agenda dédié>`, visible dans les paramètres
   de l'agenda Google). Ou ponctuellement : `uv run lea --calendar google`.

Les événements créés contiennent le motif, le téléphone, l'ancienneté, la
douleur et l'éventuelle alerte d'urgence dans leur **description**.
`credentials.json` et `token.json` sont ignorés par git : ne les partagez pas.

---

## 6. Étape 2 — Léa à la voix (depuis votre téléphone)

```
téléphone (navigateur) ──WebRTC, Wi-Fi local──▶ Mac
   micro ─▶ Silero VAD ─▶ faster-whisper small (fr, int8) ─▶ Léa (Ollama + garde-fous)
   haut-parleur ◀──────────────── Piper fr_FR-siwis (ou Kokoro ff_siwis) ◀┘
```

### Installation (une seule fois, ~1 Go à télécharger)

```zsh
cd ~/tech+ia/assistant-ia
uv sync --extra voice                 # + --extra kokoro pour la voix de secours

# Voix Piper française (63 Mo)
mkdir -p models/piper && cd models/piper
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json
cd ../..

# Modèle Whisper « small » pour faster-whisper (484 Mo)
mkdir -p models/whisper-small && cd models/whisper-small
for f in config.json tokenizer.json vocabulary.txt model.bin; do
  curl -LO https://huggingface.co/Systran/faster-whisper-small/resolve/main/$f
done
cd ../..

# Découpage des phrases (NLTK) — le Python de python.org n'a pas de certificats :
SSL_CERT_FILE=$(uv run python -c "import certifi; print(certifi.where())") \
  uv run python -c "import nltk; nltk.download('punkt_tab')"
```

> **Mac Intel** : les versions récentes de `onnxruntime`, `llvmlite` et
> `cryptography` ne sont plus publiées pour x86_64. Le `pyproject.toml`
> épingle donc `pipecat-ai==0.0.108`, `onnxruntime~=1.23.2`,
> `numba~=0.62.1`, `llvmlite~=0.45.1` et `cryptography~=48.0.1`.
> PortAudio n'est pas nécessaire : le son passe par le navigateur.

### Lancer

```zsh
open -a Ollama                        # si Ollama n'est pas déjà lancé
uv run --extra voice lea-voice
```

Le terminal affiche deux adresses, par exemple :

```
  Sur ce Mac     : https://localhost:7860
  Sur le téléphone (même Wi-Fi) : https://192.168.11.168:7860
```

Attendez la ligne `✅ Léa est prête.` (~20 s : chargement de Whisper et
préchauffage du LLM).

### Appeler depuis le téléphone

1. Le téléphone doit être sur **le même Wi-Fi** que le Mac.
2. Ouvrez l'adresse `https://192.168.x.x:7860` affichée.
3. Le certificat est auto-signé (le micro exige HTTPS), le navigateur
   affiche donc un avertissement **une seule fois** :
   - **iPhone (Safari)** : *Afficher les détails* → *visiter ce site web* →
     *Visiter le site web*.
   - **Android (Chrome)** : *Paramètres avancés* → *Continuer vers le site*.
4. Touchez **📞 Appeler Léa** et autorisez le micro.
5. Parlez normalement et laissez un court silence en fin de phrase. La
   conversation s'affiche aussi par écrit sur la page.

À la première connexion, macOS peut demander d'**autoriser les connexions
entrantes** pour Python : cliquez *Autoriser*. Sinon : Réglages Système →
Réseau → Coupe-feu → Options.

Sur le Mac lui-même, `https://localhost:7860` fonctionne aussi (même
avertissement de certificat la première fois).

### 🌍 En ligne : appeler Léa depuis n'importe où (4G/5G, autre Wi-Fi)

Le serveur reste sur votre Mac. Un tunnel **Cloudflare gratuit, sans compte**
lui donne une adresse publique `https://…trycloudflare.com` avec un vrai
certificat, donc sans avertissement sur le téléphone. Dans ce mode, le son
passe par une WebSocket à l'intérieur du tunnel. Le WebRTC direct échoue
souvent en 4G à cause du NAT des opérateurs.

**Installer cloudflared** (une fois) :

```zsh
curl -L -o /tmp/cloudflared.tgz https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz
tar -xzf /tmp/cloudflared.tgz -C ~/.local/bin && cloudflared --version
# ou, avec Homebrew : brew install cloudflared
```

**Lancer** (`caffeinate` empêche le Mac de se mettre en veille pendant ce temps) :

```zsh
open -a Ollama
caffeinate -i uv run --extra voice lea-voice --online
```

Le terminal affiche :

```
  🌍 Léa est accessible en ligne (4G/5G, n'importe quel Wi-Fi)
  Lien direct  : https://mots-au-hasard.trycloudflare.com/?code=103734
  Code d'accès : 103734
```

Envoyez-vous le **lien direct** (par exemple par message) et ouvrez-le sur
le téléphone. Le code est mémorisé par le navigateur.

- **Agenda** : ouvrez `…trycloudflare.com/agenda` (ou le lien « 📅 Voir
  l'agenda » sous le bouton d'appel). Même code. La page affiche les
  rendez-vous à venir par jour, avec le motif, la douleur, un numéro
  cliquable pour rappeler et les urgences signalées. Elle s'actualise toute
  seule toutes les 30 s. Sur le Mac, les rendez-vous sont dans
  `data/calendar.json`.
- **Sécurité** : sans le code, la page refuse l'appel. Le serveur n'écoute
  qu'en local (127.0.0.1) : seul le tunnel peut le joindre. Un seul appel à
  la fois. Pour choisir votre propre code : `--code 4821` ou `LEA_CODE=4821`
  dans `.env`.
- **L'adresse change à chaque lancement** (tunnel « rapide » gratuit). Pour
  une adresse fixe, il faut un compte Cloudflare gratuit et un nom de
  domaine (tunnel nommé).
- Le Mac doit rester allumé, connecté à Internet, avec Ollama lancé.
- Arrêter : `Ctrl+C` dans le terminal (le tunnel se ferme avec le serveur).

Tester le mode en ligne sans téléphone :

```zsh
uv run --extra voice python scripts/simuler_appel.py --ws \
  --url https://mots-au-hasard.trycloudflare.com --code 103734 "Bonjour, j'ai mal à une dent."
```

### À quoi s'attendre sur un MacBook Pro 2019

- **Délai** : 5 à 15 s entre la fin de votre phrase et la réponse
  (transcription ~2 s, LLM ~5-10 s sur CPU, voix ~1 s). Au-delà de 3,5 s,
  Léa dit « Un instant, s'il vous plaît » pour faire patienter.
- **Pas d'interruption** : tant que Léa parle, le micro est ignoré.
  Attendez qu'elle ait fini.
- La confirmation finale est dite par le code, sans passer par le LLM.

### Voix de secours : Kokoro

Si Piper pose problème :

```zsh
uv sync --extra voice --extra kokoro
uv run --extra voice --extra kokoro lea-voice --tts kokoro
```

Le modèle Kokoro (~330 Mo) se télécharge automatiquement au premier
lancement. La voix française est `ff_siwis`, modifiable avec
`KOKORO_VOICE=…`.

### Réglages (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `TTS_ENGINE` | `piper` | `piper` ou `kokoro` |
| `PIPER_VOICE` | `fr_FR-siwis-medium` | voix dans `models/piper/` |
| `WHISPER_MODEL` | `models/whisper-small` | dossier local ou nom (`small`, `base`…) |
| `VAD_STOP_SECS` | `0.8` | silence (s) qui marque la fin de votre phrase |
| `VOICE_PORT` | `7860` | port du serveur |

Pour que le modèle reste en mémoire entre deux appels (sinon Ollama le
décharge après 5 minutes d'inactivité), lancez Ollama ainsi :

```zsh
OLLAMA_KEEP_ALIVE=1h ollama serve
```

### Tester sans téléphone

Un patient virtuel (voix Piper) appelle Léa et la conversation s'affiche :

```zsh
uv run --extra voice python scripts/simuler_appel.py \
  "Bonjour, j'ai mal à une dent depuis deux jours." \
  "La douleur est à six sur dix." "Lundi prochain, le matin si possible."
```

### Autorisation du micro (si vous l'avez refusée)

- **iPhone** : Réglages → Safari → Micro → Demander (ou Autoriser).
- **Android (Chrome)** : cadenas à gauche de l'adresse → Autorisations → Micro.
- **Mac** : Réglages Système → Confidentialité et sécurité → Microphone →
  activez votre navigateur :

```zsh
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
```

---

## 7. Rapide et hébergée en ligne, gratuitement

| Rôle | Service | Gratuit |
|---|---|---|
| Écoute (Whisper large-v3) et réflexion (gpt-oss-120b, secours gpt-oss-20b et qwen3.8-27b) | Groq | oui, avec quotas |
| Serveur (détection de voix + voix Piper), adresse fixe | Hugging Face Spaces | oui |
| Agenda durable, visible dans l'app Google Agenda | Google Calendar | oui |

> **Données de santé** : la voix et la conversation passent par Groq et Hugging
> Face (hors de France). C'est adapté à des **tests**. Pour de vrais patients :
> hébergeur certifié HDS, RGPD, information des patients.

Quotas gratuits de Groq : 8 000 tokens par minute par modèle. Si le modèle
principal est saturé ou trop lent, Léa bascule toute seule sur le suivant.
Les modèles disponibles changent : `GROQ_LLM_MODEL` permet d'en choisir un autre. Le Space est public (code source visible, sans
aucune clé) et s'endort après 48 h sans visite (réveil en 1 minute environ).
Les urgences journalisées sont effacées à chaque redémarrage du Space ; un
rendez-vous pris pendant un appel d'urgence reste marqué dans l'agenda.

### a. Groq (2 min)

1. Créez un compte sur <https://console.groq.com>, puis **API Keys → Create API Key**.
2. Ajoutez la clé dans `.env` (`GROQ_API_KEY=gsk_...`). Ne la collez jamais ailleurs.

Test immédiat sur le Mac, avec le code déjà installé :

```zsh
uv run --extra voice lea-voice --online
```

### b. Google Agenda (5 min) — compte de service

1. <https://console.cloud.google.com> → créez un projet, puis **API et services →
   Bibliothèque** → activez **Google Calendar API**.
2. **IAM et administration → Comptes de service → Créer** (aucun rôle nécessaire).
3. Ouvrez le compte → **Clés → Ajouter une clé → JSON**, puis :

```zsh
mv ~/Downloads/NOM-DU-FICHIER.json ~/tech+ia/assistant-ia/service-account.json
```

4. Dans [Google Agenda](https://calendar.google.com) : paramètres de l'agenda →
   **Partager avec des personnes** → ajoutez l'adresse du compte de service
   (`...@...iam.gserviceaccount.com`) avec **Apporter des modifications aux événements**.
5. Dans `.env` : `GOOGLE_CALENDAR_ID=votre.adresse@gmail.com`.

### c. Hugging Face (3 min)

1. Créez un compte sur <https://huggingface.co/join>.
2. **Settings → Access Tokens → New token**, type **Write**, puis :

```zsh
cd ~/tech+ia/assistant-ia && .venv/bin/hf auth login
```

### d. Déployer

```zsh
uv run --extra voice python scripts/deployer_space.py --space VOTRE_NOM/lea --essai   # vérifie la liste des fichiers
uv run --extra voice python scripts/deployer_space.py --space VOTRE_NOM/lea
```

Le script crée le Space, y dépose les clés comme **secrets**, envoie le code
(liste blanche : ni `.env`, ni compte de service, ni `data/`) et affiche
l'adresse, le code d'accès (8 chiffres, gardé dans `.env`) et le lien de l'agenda.
Relancez la même commande après chaque modification du code.

---

## Structure

```
src/lea/
  config.py          paramètres (.env)
  dates.py           compréhension des dates/heures en français
  scheduling.py      horaires d'ouverture, créneaux libres
  calendars/         interface Calendar, MockCalendar (JSON), GoogleCalendar
  tools.py           get_free_slots, book_appointment, flag_urgence
  prompts.py         prompt système de Léa
  agent.py           boucle LLM + appels d'outils
  cli.py             commande `lea`
  voice/
    bot.py           pipeline Pipecat (VAD, Whisper, Léa, Piper/Kokoro)
    server.py        serveur HTTPS, WebRTC, WebSocket, tunnel (`lea-voice [--online]`)
    speech.py        texte -> parole (« 9h30 » -> « 9 heures 30 »), filtre Whisper
    static/index.html  page « Appeler Léa »
scripts/
  simuler_appel.py   appel de test sans téléphone
  deployer_space.py  déploiement sur Hugging Face Spaces
Dockerfile           image du Space
models/              voix Piper et modèle Whisper (non versionnés)
tests/               pytest
```

## Licence

MIT.
