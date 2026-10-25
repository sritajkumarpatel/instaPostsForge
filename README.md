# instaPostsForge

Posts one photo a day to Instagram. Each day it picks the next unposted photo from your folder, keeps the photo's own shape, has Claude write an English caption to suit it (adding a quote when one fits) with 3 to 5 hashtags, and publishes it through Instagram's official API.

It never asks for your Instagram password. You grant it an access token that you can revoke at any time.

## How a run works

1. **Source**: lists the photos in the profile's Google Drive folder (or a local folder).
2. **Selection**: picks the oldest photo that has not been posted yet. `state/<profile>.json` records what has gone out, so nothing repeats.
3. **Image**: converts the photo to JPEG and keeps its aspect ratio. Only a photo outside Instagram's range (4:5 portrait to 1.91:1 landscape) gets a border. Photos are never cropped.
4. **Caption**: Claude looks at the photo and writes the caption, an optional quote, hashtags and alt text. Rules are checked before posting: 3 to 5 hashtags, no generic tags, at most 2,200 characters.
5. **Publish**: uploads the JPEG to temporary storage, gives Instagram a short-lived link, publishes it, then deletes the temporary copy.

`forge preview` stops after step 4 and saves the result in `drafts/` so you can review it without posting anything.

## Project layout

```
config/profiles.yaml        one entry per Instagram account (no secrets)
.env                        your secrets, local only (copy from .env.example)
state/                      post log per profile
src/instapostsforge/
  cli.py                    the `forge` command
  pipeline.py               runs one day's post
  config.py                 loads profiles and merges defaults
  sources/                  where photos come from (google_drive, local_folder)
  imaging/                  JPEG conversion and aspect-ratio handling
  captions/                 caption writer (Claude) and rule checks
  hosting/                  temporary public URL for Instagram (S3 / Cloudflare R2)
  publishers/               Instagram API client
  selection.py              which photo goes next
tests/                      runs without any accounts or keys
.github/workflows/          daily posting job and tests
```

Every stage sits behind a small interface (`PhotoSource`, `CaptionWriter`, `ImageHost`, `Publisher`), so you can add a new photo source or storage provider without touching the rest.

## Setup

You need Python 3.10 or newer.

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"
pytest                       # should pass without any keys
cp .env.example .env         # Windows: copy .env.example .env
```

Then fill in `.env`:

| Variable | Where it comes from |
| --- | --- |
| `ANTHROPIC_API_KEY` | [Claude Console](https://console.anthropic.com/) → API keys |
| `IG_MAIN_ACCESS_TOKEN` | Your Meta app → Instagram → API setup with Instagram login → generate a token for your account |
| `GOOGLE_DRIVE_API_KEY` | Google Cloud console → enable the Drive API → Credentials → API key. Your Drive folder must be shared as "Anyone with the link can view". |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Use this instead of an API key to keep the folder private: share the folder with the service account's email address and set `auth: service_account` in the profile |
| `S3_*` | A Cloudflare R2 bucket (free tier) or any S3-compatible bucket, with an access key |

Put your Drive folder link in `config/profiles.yaml` under `profiles.main.source.folder_url`.

Before the first post:

1. Switch your Instagram account to **Creator** or **Business** (Settings → Account type and tools).
2. Create an app at [developers.facebook.com](https://developers.facebook.com/), add the **Instagram** product, and add your Instagram account as a tester. Because the app only posts to your own account, it can stay in development mode with no app review.

## Commands

```bash
forge profiles                 # list profiles
forge preview -p main          # pick + caption, save to drafts/, post nothing
forge check -p main            # verify the token, quota and photo folder
forge post -p main             # post today's photo
forge refresh-token -p main    # extend the Instagram token by 60 days
```

Instagram tokens last about 60 days. Run `forge refresh-token` about once a month and update `.env` and the GitHub secret.

## Posting automatically every day

`.github/workflows/daily-post.yml` runs the pipeline on GitHub Actions:

1. Add each `.env` value as a repository secret (Settings → Secrets and variables → Actions).
2. Run the workflow by hand in **preview** mode and download the draft from the run's artifacts.
3. Run it once in **post** mode.
4. Uncomment the `schedule` block to post daily. Cron times are in UTC.

After each post, the workflow commits `state/<profile>.json` so the next run knows what was posted.

## Adding another account or photo folder

Copy the `main` block in `config/profiles.yaml` under a new name, point it at new variable names (for example `IG_TRAVEL_ACCESS_TOKEN`) and a different `folder_url`, and add those variables to `.env`. Run it with `forge post -p travel`. To schedule it on GitHub, add its secrets to the workflow's `env` list.

Anything in `defaults` can be overridden per profile, for example `captions.account_theme`, `captions.quote`, `image.pad_color` or `selection.strategy`.
