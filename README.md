# Private event photo bot

This first version watches one private Telegram channel. It indexes each new photo's faces, lets a channel member send a single-person selfie, and returns likely matching channel photos. If the selfie misses, `/faces` shows pages of detected face groups; tapping a face searches for similar photos. `/allfaces` includes every detected face when grouping is wrong. `/more` pages through results and `/status` shows indexing progress.

The bot stores face descriptors, small face thumbnails, Telegram file IDs, and post times in SQLite. It does not save downloaded photos or selfies locally; the images sent through Telegram remain subject to Telegram's own retention. It does not verify that a selfie or selected face belongs to the person asking; any channel member can search the event collection. Tell attendees about this before rollout, and keep the channel private.

## Why this stack

Immich is excellent when you also want a full photo library: it groups faces and supports people, location, and time filters. [PhotoPrism](https://docs.photoprism.app/user-guide/organize/people/) is another self-hosted library with face and multi-person filters. Neither directly provides this Telegram selfie flow. Immich's [minimum 6 GB RAM requirement](https://docs.immich.app/install/requirements/) and separate library complicate this Telegram-first pilot. This bot uses OpenCV Zoo's [YuNet detector](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) and [SFace recognizer](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface). The models download during setup and are checked against SHA-256 values. Face groups and matches are approximate; use a clear, front-facing selfie. `MATCH_THRESHOLD` defaults to 0.45 and should be tuned with real event images.

## Set up locally

1. In Telegram, create a bot with **@BotFather**. Create a private event channel and add the bot as an administrator. The admin role lets it receive channel posts and reliably check membership.
2. Install Python 3.10 or newer and run:

   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/python -m gallery.models
   cp .env.example .env
   chmod 600 .env
   ```

3. Set `BOT_TOKEN` in `.env`. Publish one new test photo in the channel. Load `.env` with the commands below, then run `.venv/bin/python -m gallery.channel_id` to print the channel's numeric ID. Put it in `CHANNEL_ID`.
4. Start the bot with the environment loaded:

   ```sh
   set -a
   . ./.env
   set +a
   .venv/bin/python -m gallery.bot
   ```

5. Send `/start` to the bot from a Telegram account that has joined the channel, then send a selfie. Test `/faces`, a numbered face button, and `/more`. Run `/status` while a batch of channel photos is indexing.

For a large initial batch, post images to the channel in batches while the bot is running. Bot API updates do not provide arbitrary old channel history: images posted before the bot became admin must be posted again. Normal Telegram photos are compressed and may lose original EXIF data. Post as image documents if you want to preserve original metadata for a future time or location feature, while keeping each image below the Bot API's [20 MB download limit](https://core.telegram.org/bots/api#getfile). This version uses channel post time. Oversized or unreadable posts appear in `/status` as failed; inspect service logs and repost a smaller image. The database persists across restarts; back it up regularly.

## Free server option

A home laptop, desktop, or spare machine can run this long-polling bot without a public IP, as long as it remains on and can reach Telegram. This is the quickest way to run the current code from Iran. GitHub Actions alone cannot act as its always-on host.

For users who are eligible, an [Oracle Cloud Always Free Ampere A1 VM](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) can provide up to 2 OCPUs and 12 GB RAM, subject to capacity. Its 200 GB Always Free block storage includes the boot volume. However, [Oracle says its cloud services cannot be accessed from Iran](https://www.oracle.com/corporate/security-practices/corporate/governance/global-trade-compliance/), so this is not an applicable option for an Iran-based deployment. Another provider's lawful free VM can use the same Linux setup if it has persistent storage and outbound Telegram access.

### Cloudflare and Vercel

[Cloudflare Workers Free](https://developers.cloudflare.com/workers/platform/limits/) can receive Telegram webhooks, and [D1](https://developers.cloudflare.com/d1/platform/pricing/) can hold a modest face index. Its 10 ms CPU and 128 MB memory limits do not fit this OpenCV recognition code. [Cloudflare Containers](https://developers.cloudflare.com/containers/platform/pricing/) can run heavier code, but require the paid Workers plan.

[Vercel Hobby](https://vercel.com/docs/functions/limitations) is a plausible all-cloud pilot: Python Functions have up to 2 GB RAM and, with Fluid Compute, up to five minutes per invocation. Pairing a Telegram webhook with an external persistent database such as [Neon](https://vercel.com/docs/postgres) could remove the need for an always-on VM. The current bot cannot be deployed there unchanged: it uses long polling and a local SQLite file, while [Vercel's function filesystem is read-only except for temporary scratch space](https://vercel.com/docs/functions/runtimes/). Webhook processing, durable storage, background indexing, and the model bundle would need to be built and tested. Vercel's free Hobby plan is for [non-commercial personal use](https://vercel.com/docs/plans/hobby); check account availability and terms for your situation before relying on it.

On an Ubuntu VM (when one is available):

1. Create a VM, choose an SSH key, and allow outbound HTTPS. You do not need to open an inbound HTTP port for this polling bot.
2. Install Git, Python 3, and Python's `venv` package. Clone your GitHub repository to `/home/ubuntu/event-gallery` and perform the local setup above there. For a private repository, configure a read-only GitHub deploy key on the VM.
3. Set `.env` as above. Create `data/` and ensure `ubuntu` owns it. Install [deploy/event-gallery.service](deploy/event-gallery.service) at `/etc/systemd/system/event-gallery.service`. If your VM username or checkout path differs, edit that unit first.
4. Run `sudo systemctl daemon-reload`, `sudo systemctl enable --now event-gallery`, and `sudo journalctl -u event-gallery -f` to verify startup. Back up `data/gallery.sqlite3` (including its SQLite WAL files, or use SQLite's backup command) to private storage.

## GitHub CI/CD

[The workflow](.github/workflows/ci.yml) runs unit tests and Python compilation on pushes and pull requests. On `main`, it can deploy to the VM after tests pass. Create a dedicated SSH key for Actions, put its public key in `/home/ubuntu/.ssh/authorized_keys`, and add these repository secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | VM IP or hostname |
| `DEPLOY_USER` | `ubuntu` or your VM username |
| `DEPLOY_SSH_KEY` | Dedicated private SSH key for Actions |
| `DEPLOY_HOST_KEY` | The verified `known_hosts` line for that VM |

Add repository variable `DEPLOY_ENABLED=true` when ready. The deployment command pulls `main`, installs dependencies, checks models, and restarts the service. Allow the deployment user to run only `systemctl restart event-gallery` without a password through a narrow sudoers entry. Keep `.env`, `data/`, and `models/` off GitHub; they are ignored by this repository. The server needs network access to Telegram, PyPI, GitHub, and the OpenCV model host during setup.

## Boundaries and next version

This version searches one face at a time. It records post timestamps but has no time or place search command yet. A later version can add two-person intersection search and time filters from stored post times; location needs GPS EXIF from document uploads or manual venue labels. Immich can be introduced later as an admin interface or fuller library, but its face clusters do not automatically supply this bot's selfie matching flow. Face matches can be wrong, particularly for distant, turned, or small faces. The first deployment should use a small consented test set and review threshold behavior before inviting everyone.
