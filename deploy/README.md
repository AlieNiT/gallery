# Deploy the Telegram bot on an always-on Ubuntu/Debian server

These instructions assume a fresh Ubuntu 22.04/24.04 or Debian 12/13 server, SSH access as `root`, outbound HTTPS to Telegram/GitHub/PyPI, and a private Telegram channel. The bot uses long polling: **do not open an HTTP port or deploy the Flask web UI**. It runs as an unprivileged `gallery` user under systemd.

## 1. Prepare Telegram

1. The token previously shared in chat must be revoked using **@BotFather** (`/revoke`), then use the new token below. Never commit or paste it into GitHub or a support message.
2. Add the bot as an **administrator** in the private channel. This is needed for reliable membership checks.
3. Stop any other process using the same bot token. Telegram long polling and webhooks cannot run simultaneously, and two pollers will conflict.

## 2. Install the code (commands run as root on the server)

```sh
apt update
apt install -y ca-certificates git nano python3 python3-venv python3-pip sudo sqlite3
adduser --disabled-password --gecos "" gallery
install -d -o gallery -g gallery -m 755 /opt/gallery
sudo -u gallery git clone https://github.com/AlieNiT/gallery.git /opt/gallery
sudo -u gallery sh -c 'cd /opt/gallery && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m gallery.models'
```

If the repository becomes private, give this server a read-only GitHub deploy key before cloning. Model files and the Python environment stay under `/opt/gallery/`; neither is committed to Git.

## 3. Set the secret and discover the channel ID

Create a file that the `gallery` user can read but other users cannot:

```sh
umask 077
touch /etc/gallery-bot.env
chown root:gallery /etc/gallery-bot.env
chmod 0640 /etc/gallery-bot.env
nano /etc/gallery-bot.env
```

Enter these lines, replacing the token with the **new** token from BotFather. Leave `CHANNEL_ID=0` for the moment:

```ini
BOT_TOKEN=PASTE_NEW_TOKEN_HERE
CHANNEL_ID=0
DATA_DIR=/var/lib/gallery
MODEL_DIR=/opt/gallery/models
MATCH_THRESHOLD=0.45
```

Save and exit. First remove any old webhook, keeping pending updates:

```sh
sudo -u gallery sh -c 'set -a; . /etc/gallery-bot.env; set +a; cd /opt/gallery && exec .venv/bin/python -m gallery.check --webhook-only --remove-webhook'
```

Post one **new** test photo to the channel, then find its numeric ID:

```sh
sudo -u gallery sh -c 'set -a; . /etc/gallery-bot.env; set +a; cd /opt/gallery && exec .venv/bin/python -m gallery.channel_id'
```

Edit `/etc/gallery-bot.env` again and replace `CHANNEL_ID=0` with the printed negative ID (usually beginning `-100`). If no channel ID appears, verify the bot is an admin, stop any old bot instance, post another new photo, and retry. This discovery command does not discard queued updates.

Check that the bot can access the channel:

```sh
sudo -u gallery sh -c 'set -a; . /etc/gallery-bot.env; set +a; cd /opt/gallery && exec .venv/bin/python -m gallery.check'
```

The check prints the bot username and channel title/ID, never the token. Do not continue until it reports `Ready`.

## 4. Start the always-on service

```sh
install -o root -g root -m 0644 /opt/gallery/deploy/gallery-bot.service /etc/systemd/system/gallery-bot.service
systemctl daemon-reload
systemctl enable --now gallery-bot.service
systemctl status gallery-bot.service --no-pager
journalctl -u gallery-bot.service -n 80 --no-pager
```

The service stores its SQLite index in `/var/lib/gallery/gallery.sqlite3`. It restarts after crashes and at boot. It needs only outbound HTTPS; no inbound firewall rule, domain, TLS certificate, or public web server is required.

From a Telegram account that belongs to the channel, open a private chat with the bot and send `/start`, then a clear one-person selfie. Post a new channel image and check `/status`; once indexed, try the selfie again. `/faces` shows representative faces, `/allfaces` shows every detected face, and `/more` pages through results. A user outside the channel should be denied.

The local web app's `data/local/` collection is separate; **it does not sync to the bot**. Send event images to the Telegram channel after the bot is added. Telegram does not offer arbitrary old channel history to the Bot API, and pending updates are retained for at most 24 hours. Images sent as documents can preserve originals, but the public Bot API can download only files up to 20 MB. Check `/status` and service logs for failed items.

## 5. Enable GitHub Actions deployment (after the manual test works)

CI tests already run on every push. Automatic deployment is optional and disabled until you set `DEPLOY_ENABLED=true`. Use a **separate SSH key** for GitHub Actions, not your root key. Generate it on your own computer:

```sh
ssh-keygen -t ed25519 -f ~/.ssh/gallery-github-deploy -C gallery-github-actions
```

On the server, as root, create `/home/gallery/.ssh/authorized_keys` and paste the contents of `gallery-github-deploy.pub` as one line:

```sh
install -d -o gallery -g gallery -m 0700 /home/gallery/.ssh
nano /home/gallery/.ssh/authorized_keys
chown gallery:gallery /home/gallery/.ssh/authorized_keys
chmod 0600 /home/gallery/.ssh/authorized_keys
```

Test `ssh -i ~/.ssh/gallery-github-deploy gallery@SERVER_IP` from your computer. In `visudo -f /etc/sudoers.d/gallery-bot-deploy` on the server, add exactly:

```text
gallery ALL=(root) NOPASSWD: /usr/bin/systemctl restart gallery-bot.service
```

On your computer, get the server's SSH host key with `ssh-keyscan -t ed25519 SERVER_IP`, and **verify its fingerprint** against `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` run on the server before trusting it. In the GitHub repository's **Settings → Secrets and variables → Actions**, set these secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | `SERVER_IP` or hostname used in the verified host-key line |
| `DEPLOY_USER` | `gallery` |
| `DEPLOY_SSH_KEY` | Full contents of the dedicated **private** key |
| `DEPLOY_HOST_KEY` | The verified full `ssh-keyscan` line |

Then set the repository **variable** `DEPLOY_ENABLED` to `true`. A push to `main` will run tests, pull the code on the server, install any new dependencies/models, and restart the service. The token stays only in `/etc/gallery-bot.env`; GitHub Actions does not need it.

## Operations and privacy

- Follow logs: `journalctl -u gallery-bot.service -f`.
- Restart after editing `/etc/gallery-bot.env`: `systemctl restart gallery-bot.service`.
- Check queue progress: send `/status` in the bot's private chat.
- Back up the SQLite database with `sqlite3 /var/lib/gallery/gallery.sqlite3 ".backup '/root/gallery-backup.sqlite3'"` while the service is running; keep backup copies private and rotate them. Do not copy only the `.sqlite3` file while it is active, because SQLite uses WAL files.
- Any channel member can use `/faces` to browse detected faces and can search with a photo of someone else. This is **not identity verification**. Obtain attendee consent and keep the channel private.
- Face matches are approximate; start with a small consented test batch and tune `MATCH_THRESHOLD` before a full rollout.
