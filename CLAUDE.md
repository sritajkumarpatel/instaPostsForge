# CLAUDE.md

Guidance for Claude (Claude Code or any Claude session) working in this repository.

## What this project is

instaPostsForge posts once a day to an Instagram account from a Google Drive folder: one photo, or 2 to 5 photos from one outing or place as a multi-photo (carousel) post. An AI workflow decides which photo goes out and what it says. Plain code does everything with side effects: fetching, image preparation, hosting, publishing and record-keeping. Keep that split. AI output is always validated by code before it can reach Instagram.

In agentic terms it is a workflow (prompt chaining with gates, light routing by photo category, and an evaluator-optimizer review loop), not an autonomous agent. Don't give the model tools or let it choose steps without a strong reason. See `docs/ARCHITECTURE.md`.

## The workflow

`src/instapostsforge/pipeline.py` runs these steps in order:

1. **Fetch results** (code): reads Instagram insights for recent posts into `state/<profile>/insights.json`.
2. **Photo analyst** (AI): describes each new photo once and caches the notes in `state/<profile>/catalog.json`.
3. **Planner** (AI): picks today's photo from the unposted ones and chooses the caption angle. When `selection.multi_photo` is on (the `MULTI_PHOTO` env var overrides it), it may instead pick a cover plus `extra_ids` from one of the sets `grouping.py` proposes; `Run.check_extras` drops any extra outside the cover's set. If it fails, or returns an ID that isn't in the list, the run falls back to a random photo.
4. **Prepare image** (code): converts to JPEG and keeps the aspect ratio. A border is added only outside 4:5 to 1.91:1; nothing is cropped.
5. **Caption writer** (AI), then 6. **Hashtag strategist** (AI).
7. **Reviewer** (AI): approves the post, or sends it back with feedback. Caption length is code's job: `trim_caption_text` holds the text to `ai.max_caption_text_chars` (150) before review by dropping body sentences, and a reviewer complaint about length alone doesn't block a post. The number of revisions is limited by `ai.max_revisions`, and only the part it faulted is redone (`Review.failed`). The capture time (`taken_at`, from EXIF or the file name) is added to the notes by code, never by the model.
8. **Publish** (code): stages the JPEG(s) on the `media` branch, posts through the Instagram API (`publish_image`, or `publish_carousel` for a set), then deletes the staged files. A set only holds photos of one shape (`same_shape`, from the `shape` code adds to each analysis), so nothing is framed or cropped.
9. **Record** (code): writes `state/<profile>/posts.json`, keyed by a hash of the Drive file ID; a set's other photos are listed under `more_photos` and count as posted.

## Where the AI is configured

| What | Where |
| --- | --- |
| Model, effort and max tokens, per agent | `ai.model` / `ai.effort` / `ai.max_tokens` and `ai.agents.<agent>` in `config/profiles.yaml` |
| What each agent is asked to do | `src/instapostsforge/prompts/<agent>.md` (`$placeholders` are filled by `agents/claude.py`) |
| Account-wide voice and rules for every agent | `config/ai-instructions.md` (path set by `ai.instructions_file`) |
| What the account is about (niche, audience, location, voice) | `ai.account` in `config/profiles.yaml`, rendered into every prompt as `$account_notes` |
| Rules per photo category and mood | `config/playbooks/categories.md` and `moods.md`, one `## key` section each; only the photo's sections reach the caption writer, hashtag strategist and reviewer as `$playbook` |
| Content rules enforced in code | `selection.min_quality`, `skip_screenshots`, `allow_children`, `near_duplicate_days`, and `ai.planning` (category, main-subject and mood variety; a category can't follow the last post or anything posted yesterday), applied in `Run.eligible` and `Run.keep_variety` |
| Structured output schemas | Pydantic models in `src/instapostsforge/agents/claude.py` |
| Interface the pipeline depends on | `AIWorkflow` in `src/instapostsforge/agents/base.py` |

The categories and moods are fixed lists in `agents/playbooks.py` and must match the `Category` and `Mood` types in `agents/claude.py` and the headings in the playbooks; a test checks this. To add a category, change all three.

The agent names are `analyst`, `planner`, `caption_writer`, `hashtags` and `reviewer`. Use these names for config keys, usage-log rows and prompt files.

To change behaviour, prefer this order: `config/ai-instructions.md` for account-wide tone, then the agent's prompt file, then its schema, and code last. When you change a schema field, update the fake in `tests/fakes.py` and the tests in `tests/test_claude_agents.py`.

## Token audit log

Every Claude call is logged to `state/<profile>/usage/<YYYY-MM>.jsonl`, whether it succeeds or fails. Each entry records the run ID, command, agent, model, input, output and cache tokens, seconds taken, and an estimated cost from `ai.pricing`. `forge usage -p <profile> [--month YYYY-MM]` totals the log, and every run prints its own table, which also appears on the GitHub Actions summary. When you add a new AI call, route it through `ClaudeWorkflow._ask` so it gets logged.

## Rules that must not be broken

- **Instagram credentials:** never automate Instagram with a username and password, or with unofficial private APIs. Use only the official Instagram API with an access token.
- **Secrets:** never commit secrets. They live in `.env` locally and in GitHub Actions secrets. The repo is public, so `config/profiles.yaml` only names environment variables. The Drive folder link is a secret too, `DRIVE_FOLDER_<PROFILE>`.
- **GPS stays private:** `Photo.location` is only compared in memory (`grouping.py`). Never write coordinates to `state/`, logs or the summary.
- **No repeats:** a photo must never be posted twice, including the other photos of a multi-photo post. Record a post only after Instagram returns a media ID, and key it by the source's file ID, never by file name.
- **Instagram limits:** at most 5 hashtags, at most 2,200 characters, JPEG only, and an aspect ratio between 4:5 and 1.91:1. These are enforced in `captions/validator.py` and `imaging/processor.py`. Don't loosen them.
- **Quote authors:** never publish an attributed quote unless the model was certain of the author (`quote_is_attributed`).
- **Content rules:** explicit or nude photos (`is_explicit`), screenshots, photos with children (unless `allow_children`), and near-duplicates of recent posts are never posted. Otherwise the photos are handpicked, so homes, cars and belongings are fine; only addresses and number plates stay out of the text. These are enforced in `pipeline.py`, not just asked for in a prompt. Keep it that way.
- **AI fallbacks:** an AI failure must never produce a broken post. Fall back (random photo) or skip the day (`ReviewRejected`). Don't post unvalidated text.

## Working on the code

```bash
pip install -e ".[dev]"
pytest -q                 # must pass with no keys or network; AI, Drive, GitHub and Instagram are faked
forge plan -p main        # needs keys in .env; posts nothing
forge preview -p main     # full workflow into drafts/, posts nothing
```

- **Tests:** add a test for every behaviour change. Tests use the fakes in `tests/fakes.py`, never real APIs.
- **New parts:** a new photo source, image host or publisher implements the matching base class (`sources/base.py`, `hosting/base.py`, `publishers/base.py`) and is registered in that package's `build_*` function.
- **New accounts:** new accounts are new profiles in `config/profiles.yaml`, not code changes.
- **Defaults:** use the current Claude models (`claude-sonnet-5-5` by default, set in `config/profiles.yaml`). Don't hard-code a model anywhere except as the config default.
