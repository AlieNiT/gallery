# Framefinder — event photo search

Framefinder has two separate modes: a local browser MVP for hand-uploaded photos, and an always-on Telegram bot that indexes new posts in a private event channel. Both support selfie search and a browse-faces fallback. [Server deployment instructions](deploy/README.md) cover the Telegram bot.

## Run the local MVP

Install Python 3.9 or newer, then from the repository directory run the user-neutral setup script:

```sh
./setup.sh
.venv/bin/python -m gallery.web
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) on the same computer. Select one or more JPEG, PNG, or WebP event photos, click **Add photos to library**, then choose a selfie and click **Find my photos**. Uploading a batch can take a while because each photo is indexed on this computer. If a selfie has no single clear face or produces no confident match, click **Selfie missed? Browse faces**. Use **Show every detected face** if grouping hides the face you need.

The app binds to `127.0.0.1`, so it is not available to other computers. It saves event originals, previews, face data, and the index under `data/local/`; that directory is excluded from Git. Selfies are processed in memory and are not added to the library. Photos with no detected faces are still saved, but will not appear in face searches. Face matching is approximate, and the face browser exposes every detected attendee to anyone who can use this computer. Use only photos you have permission to process, and do not expose the local app to the internet without adding authentication. Back up `data/local/` if the collection matters.

Run tests with `.venv/bin/python -m unittest discover -s tests -v`. To change the local port, set `PORT` before starting the app. To change where photos are stored, set `DATA_DIR`; the local library is created in its `local/` subdirectory.

## Telegram bot

The bot watches one private Telegram channel. It indexes each new photo's faces, lets a channel member send a single-person selfie, and returns likely matching channel photos. If the selfie misses, `/faces` shows pages of detected face groups; tapping a face searches for similar photos. `/allfaces` includes every detected face when grouping is wrong. `/more` pages through results and `/status` shows indexing progress.

The bot stores face descriptors, small face thumbnails, Telegram file IDs, and post times in SQLite. It does not save downloaded photos or selfies locally; the images sent through Telegram remain subject to Telegram's own retention. It does not verify that a selfie or selected face belongs to the person asking; any channel member can search the event collection. Tell attendees about this before rollout, and keep the channel private.

## Why this stack

Immich is excellent when you also want a full photo library: it groups faces and supports people, location, and time filters. [PhotoPrism](https://docs.photoprism.app/user-guide/organize/people/) is another self-hosted library with face and multi-person filters. Neither directly provides this Telegram selfie flow. Immich's [minimum 6 GB RAM requirement](https://docs.immich.app/install/requirements/) and separate library complicate this Telegram-first pilot. This bot uses OpenCV Zoo's [YuNet detector](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) and [SFace recognizer](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface). The models download during setup and are checked against SHA-256 values. Face groups and matches are approximate; use a clear, front-facing selfie. `MATCH_THRESHOLD` defaults to 0.45 and should be tuned with real event images.

## Set up the Telegram bot locally

1. In Telegram, create a bot with **@BotFather**. Create a private event channel and add the bot as an administrator. The admin role lets it receive channel posts and reliably check membership.
2. Install Python 3.9 or newer and run `./setup.sh`. It prepares the virtual environment and models and creates a private `.env` template if needed.

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

## Always-on server and GitHub CI

Use the [server preparation guide](deploy/README.md). `setup.sh` only prepares the checkout owned by the user who runs it: it installs Python dependencies and face models, runs tests, and creates a private `.env` template if needed. It does not create accounts, install OS packages, or configure systemd. [GitHub Actions](.github/workflows/ci.yml) tests every push; automatic deployment will be configured after the server account and service layout are chosen. The bot token stays on the server, not in GitHub.

## Boundaries and next version

This version searches one face at a time. It records post timestamps but has no time or place search command yet. A later version can add two-person intersection search and time filters from stored post times; location needs GPS EXIF from document uploads or manual venue labels. Immich can be introduced later as an admin interface or fuller library, but its face clusters do not automatically supply this bot's selfie matching flow. Face matches can be wrong, particularly for distant, turned, or small faces. The first deployment should use a small consented test set and review threshold behavior before inviting everyone.
