# Daily AI -> LinkedIn draft (emailed to you)

Every day at 7pm PKT, GitHub Actions runs `scripts/daily_post.py`:
Hacker News + Hugging Face (trending models, daily papers) + Wikipedia "on this day"
-> Groq picks the most relatable story and writes drafts -> you get an email -> you post.

Each email has two drafts:
- PRIMARY: real AI news (efficiency wins, new open models, papers explained simply),
  or, if the news is weak that day (strength < 6), the fun "on this day in tech" post.
- BACKUP: the other one.

## Setup (all done in the GitHub website, no commands needed)

1. **Create a repo** (private is fine) and upload this folder's contents:
   `.github/workflows/daily-post.yml`, `scripts/daily_post.py`, `README.md`.
2. **Gmail app password** (needed so the script can send mail):
   Google Account -> Security -> turn on 2-Step Verification -> search "App passwords"
   -> create one -> copy the 16-character code.
3. **Add repo secrets**: repo -> Settings -> Secrets and variables -> Actions -> New repository secret:

   | Name | Value |
   |---|---|
   | `GROQ_API_KEY` | your Groq key |
   | `EMAIL_USER` | your Gmail address (sender) |
   | `EMAIL_APP_PASSWORD` | the 16-char app password |
   | `EMAIL_TO` | where to receive drafts (can be the same Gmail) |

4. **Test now**: repo -> Actions tab -> "Daily AI LinkedIn draft" -> Run workflow.
   Email should arrive in about a minute (check spam the first time).

## Optional: Instagram as a source (via Google)

1. https://programmablesearchengine.google.com -> Add -> "Search specific sites" -> add
   `instagram.com/*` -> create. Copy the **Search engine ID** (`cx`).
2. Google Cloud Console -> enable "Custom Search API" -> Credentials -> create an API key.
3. Add repo secrets `GOOGLE_API_KEY` and `GOOGLE_CSE_ID`.

Limits: 100 free queries/day (the script uses 4), Google only indexes some Instagram
posts, snippets are short, and Google may not offer this API to new accounts anymore.
If it isn't available, the script silently skips it. Images arrive as links, not
attachments: open the post, and credit the creator when you reuse their picture.

## Good to know

- Cron is UTC. `0 14 * * *` = 7pm Pakistan time. GitHub often delays scheduled runs by
  5-30 minutes, so the email lands around 7:00-7:30pm.
- GitHub disables scheduled workflows after 60 days without repo activity. If emails
  stop, open Actions and click "Enable workflow" (or make any small commit).
- Failed runs email you automatically from GitHub.
- Change the Groq model without editing code: add a `GROQ_MODEL` env line in the workflow.
- Tweak tone, topics or length in the `SYSTEM` prompt in `scripts/daily_post.py`.
- Always glance at the source link before posting; the model is told not to invent facts,
  but you're the one whose name is on it.

## Optional local test (PowerShell)

```powershell
$env:GROQ_API_KEY = "gsk_..."
$env:DRY_RUN = "1"
python scripts/daily_post.py
```
