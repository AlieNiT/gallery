# Preparing the Telegram bot on a server

`setup.sh` is deliberately user-neutral. Run it from any checkout owned by the account you choose for the bot. It does not create users, install OS packages, alter systemd, or need root. Account creation and the always-on service are separate server-administration steps to do after choosing the account and checkout location.

## Prerequisites

- A Linux account that owns its checkout and can reach Telegram, GitHub, and PyPI over HTTPS.
- Python 3.9 or newer with `venv` support and Git installed by the server administrator.
- A private Telegram channel where the bot is an administrator. Stop other instances of the same bot before using long polling.
- A **new** token from @BotFather: revoke the token previously shared in chat. Never put it in the repository or a shell command.

## Prepare the checkout (run as the chosen account)

```sh
git clone https://github.com/AlieNiT/gallery.git gallery
cd gallery
./setup.sh
```

The script creates `.venv/`, installs requirements, downloads and verifies the OpenCV face models in `models/`, runs the tests, and creates a mode-0600 `.env` from `.env.example` only if one does not already exist. It preserves an existing `.env`. All these paths are inside the checkout and ignored by Git where appropriate.

Edit `.env` with the **new** `BOT_TOKEN`, `CHANNEL_ID=0`, and paths appropriate to this checkout (`DATA_DIR=./data`, `MODEL_DIR=./models` work when starting from the checkout). Then, with the bot added as channel admin, stop any old bot process and run:

```sh
set -a
. ./.env
set +a
.venv/bin/python -m gallery.check --webhook-only --remove-webhook
```

Post one **new** image to the channel and run `.venv/bin/python -m gallery.channel_id`. Put the printed negative ID in `.env`, reload it with the three `set -a` / `. ./.env` / `set +a` lines, and run `.venv/bin/python -m gallery.check`. Continue only when the check prints `Ready`. For a manual smoke test, start `.venv/bin/python -m gallery.bot`; stop it with Ctrl-C when finished.

The bot uses long polling, so it needs no domain, TLS certificate, or inbound HTTP port. Its SQLite database will be at `data/gallery.sqlite3` for the default `.env`. The local web app's `data/local/` library is separate and **does not sync** to Telegram. Send event images to the channel after the bot is an admin and running. Telegram does not provide arbitrary old channel history, and pending updates expire after at most 24 hours. The hosted Bot API downloads files up to 20 MB.

## Always-on operation and CI/CD

The user-neutral checkout setup intentionally stops before installing a service. Once the server account and checkout path are chosen, configure a systemd service for that account, enable it at boot, and keep its token file private. That service should run `.venv/bin/python -m gallery.bot` from the checkout directory. Do not run both the manual process and the service at once.

[GitHub Actions](../.github/workflows/ci.yml) currently runs tests and shell syntax checks on every push. Automatic deployment is not configured yet; it will be added after the service account, checkout path, and SSH key are finalized. No server credential or bot token is required for CI.

Any channel member can browse detected faces or search with someone else's photo. This is **not identity verification**. Tell attendees, obtain consent, and keep the channel private. Face matching is approximate; start with a small consented batch and tune `MATCH_THRESHOLD` if needed.
