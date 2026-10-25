# instaPostsForge

Posts one photo a day to Instagram from a Google Drive folder. An AI workflow decides which photo goes out and what it says. Code does the fetching, posting and record-keeping.

It never asks for your Instagram password. You grant it an access token that you can revoke at any time.

## The daily workflow

| # | Step | Who | What happens |
| --- | --- | --- | --- |
| 1 | Fetch results | code | Reads reach, likes, comments, saves and shares for posts from the last 30 days. |
| 2 | Photo analyst | AI | Looks at each new photo in the Drive folder once and notes its subject, setting, mood, colours, style, quality (1 to 10) and themes. The notes are saved in `state/<profile>/catalog.json`. |
| 3 | Planner | AI | Picks today's photo from the unposted ones. It balances strong photos with variety, and leans toward what has performed well. It also chooses the angle for the caption. |
| 4 | Prepare image | code | Converts the photo to JPEG at its own aspect ratio. A border is added only if the photo is outside 4:5 to 1.91:1; nothing is cropped. |
| 5 | Caption writer | AI | Writes a hook, a caption in the photo's tone, a quote when one fits, and alt text, in English. A quote's author is kept only when the model is certain of it. |
| 6 | Hashtag strategist | AI | Picks 3 to 5 specific tags (subject, place, niche). It rotates them so the same set isn't reused, and favours tags that brought reach. |
| 7 | Reviewer | AI | Checks the caption and tags against the photo and Instagram's rules. It can send them back once with feedback. |
| 8 | Publish | code | Stages the JPEG on this repo's `media` branch, posts through the official Instagram API, then deletes the staged file. |
| 9 | Record | code | Saves the post by Drive file ID in `state/<profile>/posts.json`, so the photo is never picked again. |

Every AI step has a safe fallback. If the planner fails or names a photo that doesn't exist, a random unposted photo is used. If the reviewer rejects the post twice, nothing is posted that day and the run fails visibly. Hashtags that break a rule (too few, generic, more than 5) are sent back automatically.

## Steering the AI

| To change | Edit |
| --- | --- |
| The voice and rules for every AI step | `config/ai-instructions.md` |
| What one agent is asked to do | `src/instapostsforge/prompts/<agent>.md` |
| Which model each agent uses, its effort and token limit | `ai.model`, `ai.effort`, `ai.max_tokens` and `ai.agents.<agent>` in `config/profiles.yaml` |
| Quote policy, hashtag count, review and revisions | `ai.quote`, `ai.hashtags`, `ai.review`, `ai.max_revisions` |

The agents are `analyst`, `planner`, `caption_writer`, `hashtags` and `reviewer`. For example, to run hashtags on a cheaper model:

```yaml
ai:
  agents:
    hashtags: { model: claude-sonnet-5-5, effort: medium }
```

`CLAUDE.md` explains the architecture and the rules to keep for anyone (including Claude) working on the code.

## Token usage audit log

Every AI call is logged to `state/<profile>/usage/<YYYY-MM>.jsonl`, whether it succeeds or fails. Each entry records the run, agent, model, input, output and cache tokens, time taken, and estimated cost. Each run prints a table of its usage, which also appears on the GitHub Actions run page. For a monthly total:

```bash
forge usage -p main                  # this month
forge usage -p main --month 2026-11
```

Costs are estimates from the per-model prices in `ai.pricing`. Update those if Anthropic's prices change.

### Why the photo is staged on GitHub

Instagram only accepts a public image link. It rejects Google Drive links, even "direct download" ones. So the run commits the day's JPEG to the `media` branch of this public repo, hands Instagram the `raw.githubusercontent.com` link, and deletes the file right after posting. No storage account is needed.

### Never posting a photo twice

Posted photos are tracked by their Drive file ID, so renaming a file doesn't make it eligible again. A photo is recorded only after Instagram confirms the post. When every photo has been posted, the run stops with "add more photos". You can set `selection.when_exhausted: recycle` to start over instead.

## Project layout

```
config/profiles.yaml        accounts and AI settings (public: names secrets, never holds them)
config/ai-instructions.md   standing instructions every AI step follows
CLAUDE.md                   guidance for Claude working on this repo
.env                        your secrets, local only (copy from .env.example)
state/<profile>/            posts.json, catalog.json, insights.json, usage/ (committed after each run)
src/instapostsforge/
  pipeline.py               the daily workflow
  agents/                   AI agents: analyst, planner, caption writer, hashtag strategist, reviewer
  prompts/                  one prompt file per agent
  captions/                 Instagram's caption and hashtag rules
  sources/                  where photos come from (google_drive, local_folder)
  imaging/                  JPEG conversion and aspect-ratio handling
  hosting/                  temporary public link for Instagram (GitHub media branch)
  publishers/               Instagram API: publish, insights, token refresh
  state/                    what the workflow remembers per profile
  usage.py                  token audit log
  cli.py                    the `forge` command
tests/                      run without any accounts or keys
.github/workflows/          daily posting job and tests
```

## What you need

| Secret | Where it comes from |
| --- | --- |
| `ANTHROPIC_API_KEY` | [Claude Console](https://console.anthropic.com/) → API keys (add a few dollars of credit) |
| `IG_MAIN_ACCESS_TOKEN` | Your Meta app → Instagram → API setup with Instagram login → generate a token with `instagram_business_content_publish` and `instagram_business_manage_insights` |
| `DRIVE_FOLDER_MAIN` | The share link of your photo folder, shared as "Anyone with the link can view" |
| `GOOGLE_DRIVE_API_KEY` | Google Cloud console → enable the Drive API → Credentials → API key (restrict it to the Drive API) |

Before the first post, do two things:
1. Switch your Instagram account to **Creator** or **Business** in Settings → Account type and tools.
2. Create an app at [developers.facebook.com](https://developers.facebook.com/), add the **Instagram** product, and add your account as a tester. Because the app only posts to your own account, it can stay in development mode with no app review.

Add each secret under the repo's Settings → Secrets and variables → Actions. GitHub provides `GITHUB_TOKEN` itself.

## Running it locally

You need Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                           # passes without any keys
cp .env.example .env             # then fill it in
```

```bash
forge profiles                   # list profiles
forge plan -p main --days 7      # what the planner would post this week, with reasons
forge preview -p main            # full workflow, saved to drafts/, nothing posted
forge check -p main              # verify the token, quota and Drive folder
forge post -p main               # post today's photo (needs GITHUB_TOKEN in .env)
forge refresh-token -p main      # extend the Instagram token by 60 days
forge usage -p main              # AI tokens and estimated cost this month
```

Instagram tokens last about 60 days. Run `forge refresh-token` about once a month, then update `.env` and the GitHub secret.

## Posting automatically every day

`.github/workflows/daily-post.yml` runs the workflow on GitHub Actions:

1. Add the secrets above.
2. Go to Actions → Daily post → Run workflow, and choose **plan**, then **preview**. Download the preview from the run's artifacts. The run page also shows the caption and the planner's reasoning.
3. Run it once in **post** mode.
4. Uncomment the `schedule` block to post daily. Cron times are in UTC.

After each run, the workflow commits `state/` so the next run knows what was posted, what each photo looks like, and how past posts performed.

## Adding another account or Drive folder

Copy the `main` block in `config/profiles.yaml` under a new name. Point it at new variable names, for example `IG_TRAVEL_ACCESS_TOKEN` and `DRIVE_FOLDER_TRAVEL`, and add those to `.env` and to the workflow's `env` list. Run it with `forge post -p travel`. Any `defaults` setting can be overridden per profile, for example `ai.account_theme`, `ai.quote` or `selection.strategy`.

## Cost

With Claude Opus 5.5, expect roughly $0.10 to $0.30 per daily post (about $3 to $9 a month), plus about $0.02 the first time each photo is analysed. These are estimates: the real cost depends on how many photos are in the folder and how often the reviewer asks for a rewrite. To spend less, move some agents to `claude-sonnet-5-5` or lower their effort. `forge usage` shows where the tokens go. Everything else is free: the Instagram API, the Drive API at this volume, and GitHub Actions for a public repo.
