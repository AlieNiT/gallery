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

The bot uses long polling, so it needs no domain, TLS certificate, or inbound HTTP port. Its SQLite database will be at `data/gallery.sqlite3` for the default `.env`. The local web app's `data/local/` library is separate and **does not sync** to Telegram. Send event images to the channel after the bot is an admin and running. Pending updates expire after at most 24 hours; use the one-time backfill below for older channel posts. The hosted Bot API downloads files up to 20 MB.

## Index photos already in the channel

The bot cannot fetch arbitrary past channel history through the Bot API. For a one-time backfill, use **Telegram Desktop** on your computer, open this exact channel, choose **⋮ > Export chat history**, include **Photos** (and **Files** if you posted images as documents), choose **Machine-readable JSON**, and download the media. Keep the complete export folder together; `result.json` alone is not enough. Do not export all chats or upload this private data to GitHub.

Copy the export folder to a private location on the server **outside the repository**, for example `/home/rasta/gallery-export/` if `rasta` owns the checkout. Replace paths, username, and host as appropriate. Stop the bot's systemd service (or manual bot process) before updating; it must restart with the new code to deliver imported results. Then, as the checkout owner, run from the repo:

```sh
cd /home/rasta/gallery
git pull --ff-only
set -a
. ./.env
set +a
.venv/bin/python -m gallery.backfill --dry-run /home/rasta/gallery-export/result.json
.venv/bin/python -m gallery.backfill /home/rasta/gallery-export/result.json
```

The dry run checks the channel ID and counts files before indexing. The import is resumable, skips ready posts, repairs pending or failed ones, and does not repost to the channel. It also keeps private copies under `data/album-media/` for album delivery. Restart the bot service after import. Matching photos are sent in albums of up to 10; documents form separate albums. A missing retained file or oversized export is still copied from the original channel post, so keep the bot as channel admin and do not delete those posts. Test with one known older photo after import. Exported media can be removed from the server after verifying results, but back up `data/` because it now contains retained images as well as the database. The bot continues indexing new posts automatically while it runs.

If the channel was already backfilled by an older version, stop the bot and rerun the same `gallery.backfill` command after updating. It will **not** re-index ready photos; it will prepare their album media in `data/album-media/`. Then restart the bot. Keep the Telegram Desktop export until this preparation succeeds.

The bot automatically adds per-user sent history and saved-query fields to an existing SQLite database on startup. Back up `data/gallery.sqlite3` before upgrading. Histories cannot include photos delivered before this version; each member should send a fresh selfie or choose a face once before using `/update`. New images reset that member's history, and `/reset-history` clears it while retaining the current face query.

## Always-on operation and CI/CD

The user-neutral checkout setup intentionally stops before installing a service. Once the server account and checkout path are chosen, configure a systemd service for that account, enable it at boot, and keep its token file private. That service should run `.venv/bin/python -m gallery.bot` from the checkout directory. Do not run both the manual process and the service at once.

[GitHub Actions](../.github/workflows/ci.yml) currently runs tests and shell syntax checks on every push. Automatic deployment is not configured yet; it will be added after the service account, checkout path, and SSH key are finalized. No server credential or bot token is required for CI.

Any channel member can browse detected faces or search with someone else's photo. This is **not identity verification**. Tell attendees, obtain consent, and keep the channel private. Face matching is approximate; start with a small consented batch and tune `MATCH_THRESHOLD` if needed.
