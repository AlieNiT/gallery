"""Print channel IDs from unconfirmed Telegram updates during setup."""

import os

from .bot import Telegram


def main():
    updates = Telegram(os.environ["BOT_TOKEN"]).call(
        "getUpdates", {"timeout": 2, "allowed_updates": '["channel_post"]'})
    ids = sorted({update["channel_post"]["chat"]["id"] for update in updates
                  if "channel_post" in update})
    if ids:
        for channel_id in ids:
            print(channel_id)
    else:
        print("No channel post seen. Add the bot as channel admin, publish a new photo, and retry.")


if __name__ == "__main__":
    main()
