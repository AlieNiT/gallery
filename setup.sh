#!/usr/bin/env bash
# Prepare this checkout for the local web app and Telegram bot.

set -Eeuo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd "$project_dir"

if ! command -v python3 >/dev/null 2>&1; then
    printf 'Python 3.9 or newer is required. Install it, then rerun setup.sh.\n' >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    printf 'Python 3.9 or newer is required.\n' >&2
    exit 1
fi
if [[ ! -w "$project_dir" ]]; then
    printf 'This checkout is not writable by the current user. Fix its ownership and rerun setup.sh.\n' >&2
    exit 1
fi

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m gallery.models
.venv/bin/python -m unittest discover -s tests -q

if [[ -L .env ]]; then
    printf '.env must not be a symlink. Create a regular private file instead.\n' >&2
    exit 1
fi
if [[ ! -e .env ]]; then
    install -m 0600 .env.example .env
    printf 'Created a private .env template; add your Telegram token and channel ID before starting the bot.\n'
else
    printf 'Kept your existing .env unchanged.\n'
fi

printf '\nSetup complete in %s\n' "$project_dir"
printf 'Local web app: .venv/bin/python -m gallery.web\n'
printf 'Telegram setup and always-on service: see deploy/README.md\n'
