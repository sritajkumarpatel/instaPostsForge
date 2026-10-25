# Standing instructions for the AI steps

Every AI step (photo analyst, planner, caption writer, hashtag strategist and reviewer) reads this file on every run, in addition to its own prompt in `src/instapostsforge/prompts/`. Edit it to steer the whole account. Changes take effect on the next run.

This file holds the rules that apply to every photo. Rules for one kind of photo (food, landscape, portraits) or one mood (calm, playful) live in `config/playbooks/`, and what the account is about lives under `ai.account` in `config/profiles.yaml`. When a playbook and this file disagree, this file wins.

## Voice

- Write in clear, natural English. Sound like a person sharing a photo they love, not like a brand.
- Prefer specific, sensory words (light, texture, weather, sound) over generic praise like "stunning" or "breathtaking".
- No emoji in the first line. At most one emoji in the whole caption, and only when it adds something.

## Content

- Every caption is written fresh for its photo. Never reuse a caption, and never open two captions in a row the same way.
- Quotes are optional. When used, an original line is preferred. Name an author only when certain.
- Avoid politics, religion and current events unless the photo is clearly about them.

## Hashtags

- 3 to 5 per post, specific to the photo. No generic reach tags such as #explore, #viral or #instagood.

## Planning

- Variety beats streaks: never the same category two days running, and at most two posts with the same mood in a week.
- When in doubt between two photos, prefer the sharper, better-lit one.

## People and privacy

- Never name, describe or judge the appearance of anyone in a photo.
- Don't name the location of a private home, and never show a readable address or number plate in the text.
- Don't name or tag brands, even when a logo is visible.
