# Architecture

instaPostsForge is an **AI workflow**: plain code runs a fixed sequence of steps every day, and calls Claude at five of them. The AI never decides which steps run or touches Instagram directly. Code validates everything the AI produces before it can be posted.

## System overview

```mermaid
flowchart TB
    Drive[("Google Drive folder<br/>your photos")]
    Config["Your settings in the repo<br/>config/profiles.yaml · config/ai-instructions.md · prompts/*.md"]
    Actions["GitHub Actions<br/>Daily post workflow (forge post)"]
    Claude["Claude API<br/>analyst · planner · caption writer · hashtags · reviewer"]
    State[("state/#lt;profile#gt;/ in the repo<br/>posts · catalog · insights · usage")]
    Media[("media branch<br/>today's JPEG for about a minute")]
    IG["Instagram API<br/>official, token based"]

    Drive -->|"list and download photos"| Actions
    Config -->|"read each run"| Actions
    Actions <-->|"structured requests and answers"| Claude
    Actions <-->|"read memory, commit updates"| State
    Actions -->|"stage, then delete"| Media
    Actions -->|"publish post, read insights"| IG
    Media -->|"Instagram downloads the image"| IG
```

Instagram only accepts a public image link and rejects Google Drive links, so the day's photo is staged on the repo's `media` branch while Instagram downloads it.

## The daily run

```mermaid
flowchart TD
    Start([Scheduled or manual run]) --> Insights[Fetch insights for recent posts]
    Insights --> List[List Drive photos and remove those already posted]
    List --> Empty{Any unposted photos?}
    Empty -- no --> Stop([Stop: add more photos])
    Empty -- yes --> Analyst[["AI: Photo analyst<br/>new photos only"]]
    Analyst --> Sets[Group photos into sets<br/>same outing or place and same shape, if multi-photo is on]
    Sets --> Planner[["AI: Planner<br/>picks today's photo, or 2 to 5 from one set, and the angle"]]
    Planner --> ValidPick{Valid photo ID?}
    ValidPick -- no --> Random[Fall back to a random unposted photo]
    ValidPick -- yes --> Prepare
    Random --> Prepare[Prepare JPEGs: 1080 wide, quality 95, colour profile kept<br/>trim a 3:4 portrait to 4:5, never a border<br/>a set's photos all share one shape]
    Prepare --> Writer[["AI: Caption writer"]]
    Writer --> Trim[Trim the caption to 150 characters<br/>by dropping body sentences]
    Trim --> Tags[["AI: Hashtag strategist"]]
    Tags --> Rules{"Passes Instagram rules?<br/>3 to 5 tags, 2,200 chars"}
    Rules -- no --> Retry{Revisions left?}
    Rules -- yes --> Reviewer[["AI: Reviewer"]]
    Reviewer -- "revise (length alone doesn't count)" --> Retry
    Retry -- yes, with feedback --> Writer
    Retry -- no --> Skip([No post today])
    Reviewer -- approve --> Mode{Mode}
    Mode -- preview --> Draft([Save draft to drafts/])
    Mode -- post --> Stage[Stage JPEG on media branch]
    Stage --> Publish[Publish via Instagram API]
    Publish --> Cleanup[Delete staged JPEG]
    Cleanup --> Record[Record post by Drive file ID]
    Record --> Commit([Commit state/ to the repo])
```

Double-bordered boxes are Claude calls. Each one is logged to `state/<profile>/usage/` with its tokens and estimated cost, and every run's calls are summarised by phase in a token audit report uploaded as a run artifact.

## Code layout

```mermaid
flowchart LR
    CLI["cli.py<br/>forge commands"] --> Pipeline["pipeline.py<br/>the workflow"]
    Pipeline --> Sources["sources/<br/>google_drive · local_folder"]
    Pipeline --> Agents["agents/<br/>AIWorkflow interface<br/>workflow.py + clients/"]
    Agents --> Prompts["prompts/*.md"]
    Agents --> Usage["usage.py<br/>token audit log"]
    Pipeline --> Rules["captions/validator.py<br/>Instagram rules"]
    Pipeline --> Imaging["imaging/<br/>JPEG and aspect ratio"]
    Pipeline --> Hosting["hosting/<br/>github media branch"]
    Pipeline --> Publishers["publishers/<br/>Instagram API"]
    Pipeline --> StateMod["state/<br/>posts · catalog · insights"]
```

Every box under `pipeline.py` sits behind a small interface (`PhotoSource`, `AIWorkflow`, `ImageHost`, `Publisher`). The tests swap in fakes, so they run without any accounts.

## Which agentic pattern is this?

In the terms of Anthropic's [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents), this is a **workflow**: "LLMs and tools orchestrated through predefined code paths". It is not an autonomous **agent**, where the model decides its own steps and tool calls in a loop. A daily post has well-known steps, so a workflow is cheaper, more predictable and easier to test. That follows the guidance to use the simplest design that works.

It combines these building blocks and patterns:

| Pattern | Where it appears |
| --- | --- |
| **Augmented LLM** (the building block) | Each Claude call gets structured outputs (Pydantic schemas) and memory: the photo catalog, post history and insights in `state/`. No tools are exposed to the model. |
| **Prompt chaining** | Analyst → planner → caption writer → hashtag strategist → reviewer. Each step's output is the next step's input, with programmatic **gates** in between: the planner's photo IDs must exist (and a set's extra photos must come from the cover's set), and captions and tags must pass `captions/validator.py`. |
| **Evaluator-optimizer** | The reviewer (evaluator) approves a post or returns feedback. The caption writer and hashtag strategist (optimizers) then revise. The loop is bounded by `ai.max_revisions` (2), only the part that failed is redone, and a rule failure also feeds back as feedback. Anything code can measure stays out of the loop: code holds the caption to `ai.max_caption_text_chars` (150) before review, so the reviewer never sends a post back for length alone. After the first round the reviewer can't move the goalposts: only its first concerns and the hard checks (accuracy, quote, safety) still count, and only a hard check left failing skips the day. The capture date from EXIF or the file name is passed in as `taken_at`, so the evaluator can verify dates the writer uses. |
| **Routing** (light) | The analyst classifies each photo into one of 12 categories and 8 moods. Code then routes only that category's and mood's playbook (`config/playbooks/`) to the caption writer, hashtag strategist and reviewer. The path stays the same; the instructions change per photo. |
| **Guardrails and fallbacks** | Code, not the model, enforces Instagram's limits and the content rules: it skips explicit photos, screenshots, photos with children (unless allowed) and near-duplicates of recent posts, never repeats a main subject within 3 days, limits each mood to 2 posts a week (and keeps categories apart when `ai.planning.no_repeat_category` is on), caps multi-photo posts and keeps their photos one shape, drops quotes on food and animal posts, keeps addresses and number plates out of the text, trims captions to their length limit, refuses unverified quote authors, falls back to a random photo when the planner fails, and skips the day rather than post an unapproved caption. A failed reviewer checklist item always means revise. |
| **Clear failures** | When Meta rejects a call, the error names the code and the fix (expired token, blocked app, missing permission, rate limit). Nothing is recorded as posted until Instagram returns a media ID. |
| **Human in the loop** (optional) | `forge plan` and `forge preview` (or those modes in the Actions workflow) show the AI's choices without posting. |

Patterns it deliberately does **not** use (yet):

| Pattern | Why not, or how it could be added |
| --- | --- |
| Parallelization | Photo analyses run one after another. They could run in parallel (sectioning) or via the Message Batches API at half the price. Generating three captions and letting the reviewer pick one would be parallelization by voting. |
| Orchestrator-workers | The planner orders photos but doesn't spawn sub-tasks; the steps are fixed in code. |
| Autonomous agent | The model never chooses which step comes next or calls Instagram itself. This keeps a public account safe from an unexpected loop or post. |
