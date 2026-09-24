"""Check Telegram configuration without printing the bot token."""

import argparse
import os

from .bot import Telegram


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove-webhook", action="store_true",
                        help="remove an existing webhook without discarding queued updates")
    parser.add_argument("--webhook-only", action="store_true",
                        help="check webhook state before the channel ID is known")
    args = parser.parse_args()

    token = os.environ.get("BOT_TOKEN")
    channel = os.environ.get("CHANNEL_ID")
    if not token:
        parser.error("BOT_TOKEN must be set")
    if not args.webhook_only:
        if not channel:
            parser.error("CHANNEL_ID must be set")
        try:
            channel_id = int(channel)
        except ValueError:
            parser.error("CHANNEL_ID must be a numeric Telegram chat ID")

    telegram = Telegram(token)
    webhook = telegram.call("getWebhookInfo")
    if webhook.get("url"):
        if not args.remove_webhook:
            parser.error("a webhook is configured; rerun with --remove-webhook to use long polling")
        telegram.call("deleteWebhook", {"drop_pending_updates": "false"})
        print("Removed webhook; pending updates were kept.")
    if args.webhook_only:
        print("Long polling is available; pending updates were kept.")
        return

    me = telegram.call("getMe")
    chat = telegram.call("getChat", {"chat_id": channel_id})
    if chat.get("type") != "channel" or chat.get("id") != channel_id:
        parser.error("CHANNEL_ID does not identify the expected channel")
    member = telegram.call("getChatMember", {"chat_id": channel_id, "user_id": me["id"]})
    if member.get("status") not in {"administrator", "creator"}:
        parser.error("the bot must be an administrator in the channel")
    print(f"Ready: @{me['username']} is an administrator in {chat.get('title', 'the channel')} "
          f"({channel_id}); long polling is available.")


if __name__ == "__main__":
    main()
