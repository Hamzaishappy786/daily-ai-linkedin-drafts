# Daily LinkedIn drafts (emailed to you)

Every day at 7pm PKT, GitHub Actions runs `scripts/daily_post.py`. It picks random content
modes, has Groq write a LinkedIn post for each, and emails you the best two (PRIMARY + BACKUP).
You copy, check, post.

## Modes (random, weighted)

| Mode | What you get | Real image from |
|---|---|---|
| NEWS | Mainstream-friendly AI/tech news (The Verge, TechCrunch, Ars, Hugging Face, Hacker News, new GitHub repos). Corporate and jargon-heavy items are rejected; the model must rate "wow factor" 7+/10 or the mode is skipped. | the article's own preview image |
| TIP | Practical beginner-friendly dev/AI tip | none (text) |
| QUOTE | Real quote from a computing legend + modern take (quotes are hard-coded, never invented) | author's Wikipedia photo |
| FUNNY | Relatable developer/AI humour | none (text) |
| MOTIVATION | Honest motivation for students/early-career people | none (text) |
| HISTORY | "On this day" in tech/AI (Wikipedia) | the Wikipedia page's image |

Images are the sources' own preview pictures, attached to the email (png/jpg/gif only).
Easiest way to use them: include the source link in your LinkedIn post and LinkedIn shows
the preview image by itself. If you reuse a picture separately, credit the source
(Wikipedia photos: credit Wikimedia Commons).

Instagram is not used: it blocks automated access and Google's search API is closed to new projects.

## Setup

Repo secrets (Settings -> Secrets and variables -> Actions):
`GROQ_API_KEY`, `EMAIL_USER`, `EMAIL_APP_PASSWORD` (Gmail app password), `EMAIL_TO`.
Test: Actions tab -> "Daily AI LinkedIn draft" -> Run workflow.

## Customising

- Mode frequency: `WEIGHTS` in the script. Quotes/topics: `QUOTES` and `SEEDS`.
- Voice and banned words: `STYLE`. News strictness: the NEWS task text and the `appeal` threshold (7).
- News sources: `RSS_FEEDS` and the `from_*` functions.
- Test one mode: add `FORCE_MODE: "QUOTE"` under `env:` in the workflow, then Run workflow.
- Cron is UTC (`0 14 * * *` = 7pm PKT). GitHub may delay runs 5-30 minutes.
- GitHub disables scheduled workflows after 60 days without repo activity; click
  "Enable workflow" if emails stop.

## Making the profile look professional

- Headline: what you do + what you're into, e.g. "CS student | building with LLMs & Python | sharing what I learn".
- Clear profile photo, simple banner, a 3-4 line About, best GitHub projects in Featured.
- Post 3-4 times a week, not 7. Reply to comments in the first hour; comment on 3-5 other posts daily.
- Always read the draft and the source before posting, and edit it to sound like you.
