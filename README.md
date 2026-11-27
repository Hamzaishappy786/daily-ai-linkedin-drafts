# Daily LinkedIn drafts (emailed to you)

Every day at 7pm PKT, GitHub Actions runs `scripts/daily_post.py`. It picks random content
modes, has Groq write a LinkedIn post for each, and emails you the best two (PRIMARY + BACKUP).
You copy, check, post.

## Modes (random, weighted)

| Mode | What you get | Image card |
|---|---|---|
| NEWS | Relatable AI/dev news from Hacker News, Hugging Face (models + papers), new GitHub repos. Corporate news is rejected. | no |
| TIP | Practical beginner-friendly dev/AI tip | yes |
| QUOTE | Real quote from a computing legend + modern take (quotes are hard-coded, never invented) | yes |
| FUNNY | Relatable developer/AI humour | yes |
| MOTIVATION | Honest motivation for students/early-career devs | yes |
| HISTORY | "On this day" in tech/AI (Wikipedia) | no |

Image cards are 1080x1080 PNGs attached to the email. Upload them with the post.
If a mode has nothing good that day (e.g. a dull news day) the script tries another mode.

## Setup

Repo secrets (Settings -> Secrets and variables -> Actions):
`GROQ_API_KEY`, `EMAIL_USER`, `EMAIL_APP_PASSWORD` (Gmail app password), `EMAIL_TO`.
(The old `GOOGLE_*` secrets are no longer used; you can delete them.)

Test: Actions tab -> "Daily AI LinkedIn draft" -> Run workflow.

## Customising

- Put your name on image cards: set `CARD_FOOTER: "Your Name"` in the workflow file.
- Change mode frequency: edit `WEIGHTS` in the script.
- Add quotes / topics: edit `QUOTES` and `SEEDS` in the script.
- Change the voice or banned words: edit `STYLE` in the script.
- Test one mode: set `FORCE_MODE` (e.g. `QUOTE`) in the workflow env, then Run workflow.
- Cron is UTC (`0 14 * * *` = 7pm PKT). GitHub may delay runs 5-30 minutes.
- GitHub disables scheduled workflows after 60 days without repo activity; click
  "Enable workflow" if emails stop.

## Making the profile look professional (the posts are only half of it)

- Headline: say what you do and what you're into, not just "Student at X".
  Example: "CS student | building with LLMs & Python | sharing what I learn".
- Profile photo: clear face, plain background. Add a simple banner (name + 3 skills).
- About section: 3-4 short lines (what you build, what you're learning, how to reach you).
- Featured section: pin your best GitHub projects.
- Post 3-4 times a week, not 7. Quality and consistency beat volume.
- Reply to every comment in the first hour, and comment on 3-5 other people's posts daily.
- Always read the draft and the source before posting, and edit it to sound like you.
