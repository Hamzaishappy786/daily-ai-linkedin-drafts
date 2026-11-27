#!/usr/bin/env python3
"""Daily LinkedIn draft emailer.

Every run picks a few random content "modes" (news, tip, quote, funny, motivation,
tech history), has Groq write a LinkedIn post for each, renders an image card where it
helps, and emails you the best two. You copy-paste and post.

Env vars:
  GROQ_API_KEY        (required)
  EMAIL_USER, EMAIL_APP_PASSWORD   Gmail sender + app password (unless DRY_RUN)
  EMAIL_TO            recipient, defaults to EMAIL_USER
  GROQ_MODEL          optional model override (auto-picked otherwise)
  CARD_FOOTER         optional text at the bottom of image cards, e.g. your name
  FORCE_MODE          optional: NEWS|TIP|QUOTE|FUNNY|MOTIVATION|HISTORY (for testing)
  DRY_RUN=1           print instead of emailing; saves cards as dryrun_*.png
"""
import datetime as dt
import io
import json
import os
import random
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

MODEL = os.environ.get("GROQ_MODEL")
PREFERRED = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b",
             "llama-3.1-8b-instant"]
NOT_CHAT = ("whisper", "guard", "tts", "orpheus", "embed", "safeguard")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
UA = "daily-ai-post-bot/2.0"
TODAY = dt.date.today()
DAY = TODAY.toordinal()

# ------------------------------------------------------------- static content
# Verified, well-known quotes. The model never invents a quote; it only writes around these.
QUOTES = [
    ("Talk is cheap. Show me the code.", "Linus Torvalds"),
    ("Premature optimization is the root of all evil.", "Donald Knuth"),
    ("Simplicity is prerequisite for reliability.", "Edsger W. Dijkstra"),
    ("The only way to learn a new programming language is by writing programs in it.",
     "Dennis Ritchie"),
    ("We can only see a short distance ahead, but we can see plenty there that needs to be done.",
     "Alan Turing"),
    ("Debugging is twice as hard as writing the code in the first place. Therefore, if you "
     "write the code as cleverly as possible, you are, by definition, not smart enough to debug it.",
     "Brian Kernighan"),
    ("The best way to predict the future is to invent it.", "Alan Kay"),
    ("Make it work, make it right, make it fast.", "Kent Beck"),
    ("Programs must be written for people to read, and only incidentally for machines to execute.",
     "Abelson & Sussman, SICP"),
    ("Any sufficiently advanced technology is indistinguishable from magic.", "Arthur C. Clarke"),
    ("The cheapest, fastest, and most reliable components are those that aren't there.",
     "Gordon Bell"),
    ("A ship in port is safe, but that's not what ships are built for.", "Grace Hopper"),
    ("The real problem is not whether machines think but whether men do.", "B. F. Skinner"),
    ("The Analytical Engine has no pretensions whatever to originate anything. It can do "
     "whatever we know how to order it to perform.", "Ada Lovelace, 1843"),
]

SEEDS = {
    "MOTIVATION": [
        "consistency beats intensity when learning to code",
        "shipping a small imperfect project beats planning a perfect one",
        "everyone you admire in tech once had to google the basics",
        "comparing your day 1 to someone else's year 5",
        "learning in public: sharing what you learn helps you and others",
        "reading other people's code makes you better faster than tutorials",
        "AI tools raise the floor, but fundamentals decide who keeps growing",
        "feeling lost in a new tech stack is a phase, not a verdict on you",
        "30 minutes a day for a year compounds into real skill",
        "ask questions early: being stuck for hours costs more than asking",
    ],
    "FUNNY": [
        "AI writes the code, you debug it",
        "'it works on my machine'",
        "AI confidently using a library that does not exist",
        "the five stages of debugging",
        "reading your own code from six months ago",
        "tutorial hell versus actually building something",
        "naming a variable is the hardest problem in computer science",
        "git commit messages at 2am",
        "prompt engineering is just asking clearly, but with more steps",
        "the demo that worked perfectly until the meeting started",
    ],
    "TIP": [
        "a git habit that saves you from losing work",
        "a prompting trick to get better code out of an LLM",
        "rubber duck debugging: explain the problem out loud",
        "reading a stack trace from the bottom up",
        "how to run a small LLM locally with Ollama",
        "a free resource for learning ML fundamentals",
        "a GitHub profile README that recruiters actually notice",
        "writing a good commit message",
        "a Python standard-library module most beginners miss",
        "how to ask a technical question so people actually answer",
    ],
}
WEIGHTS = {"NEWS": 30, "TIP": 16, "QUOTE": 16, "FUNNY": 16, "MOTIVATION": 12, "HISTORY": 10}
NEEDS_CARD = {"QUOTE", "FUNNY", "MOTIVATION", "TIP"}


def get_json(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300].replace("\n", " ")
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None


# ----------------------------------------------------------- news collectors
def from_hn():
    since = int(time.time()) - 36 * 3600
    out = {}
    for q in ["LLM", "open source model", "local inference", "AI agent", "GPU quantization"]:
        flt = urllib.parse.quote(f"created_at_i>{since},points>20")
        url = (f"https://hn.algolia.com/api/v1/search?query={urllib.parse.quote(q)}"
               f"&tags=story&numericFilters={flt}&hitsPerPage=10")
        for h in get_json(url).get("hits", []):
            out[h["objectID"]] = {
                "source": "HackerNews", "title": h.get("title", ""),
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                "score": h.get("points", 0), "note": ""}
    return list(out.values())


def from_hf_models():
    return [{"source": "HF trending model", "title": m.get("id", ""),
             "url": f"https://huggingface.co/{m.get('id', '')}", "score": m.get("likes", 0),
             "note": f"task={m.get('pipeline_tag')}, downloads={m.get('downloads')}"}
            for m in get_json("https://huggingface.co/api/models?sort=trendingScore&limit=12")]


def from_hf_papers():
    out = []
    for e in get_json("https://huggingface.co/api/daily_papers")[:12]:
        p = e.get("paper", {})
        out.append({"source": "HF Papers", "title": p.get("title") or e.get("title", ""),
                    "url": f"https://huggingface.co/papers/{p.get('id', '')}",
                    "score": p.get("upvotes", 0),
                    "note": (p.get("summary") or "")[:250].replace("\n", " ")})
    return out


def from_github_new_repos():
    since = (TODAY - dt.timedelta(days=7)).isoformat()
    q = urllib.parse.quote(f"topic:llm created:>{since} stars:>50")
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    d = get_json(f"https://api.github.com/search/repositories?q={q}&sort=stars&per_page=10",
                 headers=headers)
    return [{"source": "GitHub new repo", "title": r["full_name"], "url": r["html_url"],
             "score": r["stargazers_count"],
             "note": (r.get("description") or "")[:250]} for r in d.get("items", [])]


TECH_WORDS = ("comput", "software", "internet", "robot", "artificial", "chess", "program",
              "turing", "microsoft", "apple", "google", "intel", "ibm", "linux", "web",
              "satellite", "nasa", "technolog", "invent", "microprocessor", "network",
              "email", "browser", "neural", "algorithm", "supercomputer", "gpu", "openai")


def from_history():
    d = get_json("https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/"
                 f"{TODAY.month:02d}/{TODAY.day:02d}")
    out = []
    for kind in ("events", "births", "deaths"):
        for e in d.get(kind, []):
            text = e.get("text", "")
            if any(w in text.lower() for w in TECH_WORDS):
                page = (e.get("pages") or [{}])[0]
                out.append({"kind": kind[:-1], "year": e.get("year"), "text": text[:220],
                            "url": page.get("content_urls", {}).get("desktop", {}).get("page", "")})
    return out[:25]


def safe(fn):
    try:
        return fn()
    except Exception as ex:  # one dead source must not kill the run
        print(f"[warn] {fn.__name__} failed: {ex}", file=sys.stderr)
        return []


# --------------------------------------------------------------------- groq
STYLE = """You ghostwrite LinkedIn posts for a CS student / junior developer who wants a
professional, interesting profile. Audience: students and early-career engineers.

VOICE: friendly, confident, plain English, like a sharp peer. Professional, never corporate
or cringe. Short sentences.

HARD RULES
- Never invent facts, numbers, quotes, projects, jobs or personal experiences. Do not write
  "I built / I worked at / my manager". Only use facts given to you or well-established
  common knowledge. Exception: FUNNY posts may use generic relatable "me:" humour.
- First line (the hook) is at most 12 words and makes someone stop scrolling: a surprising
  fact, a contrast, or a sharp question. Never open with "In today's...", "Excited to share",
  "Let's dive in", "Did you know".
- Banned words: game-changer, revolutionary, unlock, delve, landscape, leverage, journey,
  "in the world of".
- Layout: one idea per line, blank line between blocks, 700-1100 characters total, no em
  dashes, at most 2 emojis, no bullet spam (max 4 bullets).
- End with ONE specific question that invites replies (not "thoughts?"), then a final line
  of 3-4 relevant hashtags.
Return ONLY a JSON object with keys exactly as requested."""

SCHEMA = ('{"headline": "<6-10 word title, for the email subject>", '
          '"post": "<the full LinkedIn post>", '
          '"card_text": "<one punchy line, max 110 characters, for an image card>", '
          '"source_url": "<url or empty string>"}')


def resolve_model():
    if MODEL:
        return MODEL
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        ids = [m["id"] for m in json.load(r)["data"]]
    for p in PREFERRED:
        if p in ids:
            return p
    chat = [i for i in ids if not any(x in i for x in NOT_CHAT)]
    if not chat:
        raise RuntimeError(f"No chat model available. Models seen: {ids}")
    return sorted(chat)[0]


def call_groq(model, task, temperature=0.8):
    body = json.dumps({
        "model": model, "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": STYLE},
                     {"role": "user", "content": task + "\n\nReturn JSON: " + SCHEMA}],
    }).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
                 "Content-Type": "application/json", "User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(json.load(r)["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            raise RuntimeError(f"Groq error {e.code}: {e.read().decode()[:300]}")


# -------------------------------------------------------------- mode writers
_news_cache = {}


def get_news():
    if "n" not in _news_cache:
        items = (safe(from_hn) + safe(from_hf_models) + safe(from_hf_papers)
                 + safe(from_github_new_repos))
        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        _news_cache["n"] = items[:30]
    return _news_cache["n"]


def write_post(mode, model):
    """Return dict(mode, headline, post, card_text, source_url, card_author) or None."""
    seed = None
    quote = None
    if mode == "NEWS":
        news = get_news()
        if not news:
            return None
        task = (
            "MODE: NEWS. From the items below pick ONE that a student would find genuinely "
            "amazing: models running on tiny hardware, big speed/size/cost wins, new open "
            "models, tools they can try today, a paper with a clear plain-English result. "
            "REJECT corporate news (valuations, hires, funding, lawsuits, partnerships) and "
            "anything too niche to explain in two lines. If nothing is good, return "
            '{"headline": "", "post": "", "card_text": "", "source_url": ""}.\n'
            "Structure: hook with the surprising fact; what happened (2-3 lines); why it "
            "matters to a developer or student (1-2 lines); source URL on its own line; "
            "question; hashtags.\n\nITEMS:\n" + json.dumps(news, ensure_ascii=False))
    elif mode == "HISTORY":
        hist = safe(from_history)
        if not hist:
            return None
        task = (
            f"MODE: HISTORY. Today is {TODAY:%d %B}. From these 'on this day' items pick the "
            "single most interesting tech/computing/AI one, ideally one that connects to "
            "today's AI world. Write a fun 'On this day' post. Mention how many years ago "
            "it was (current year is " + str(TODAY.year) + "). Use only the facts given. "
            "Put the source URL on its own line.\n\nITEMS:\n"
            + json.dumps(hist, ensure_ascii=False))
    elif mode == "QUOTE":
        quote = QUOTES[DAY % len(QUOTES)]
        task = (
            f'MODE: QUOTE. Quote: "{quote[0]}" by {quote[1]}.\n'
            "Write a post that opens with the quote in quotation marks plus the author on "
            "the next line, then 3-4 short lines connecting it to a modern developer's life "
            "(AI tools, code review, learning, shipping). Do not claim the author said "
            "anything beyond this quote. card_text must be the quote itself.")
    else:
        seed = SEEDS[mode][DAY % len(SEEDS[mode])]
        extra = {
            "MOTIVATION": "Write an honest motivational post for students and early-career "
                          "devs. No fake success stories; use a universal observation or a "
                          "simple analogy. Practical, not preachy.",
            "FUNNY": "Write a genuinely funny, relatable developer/AI joke post: a tight "
                     "setup and punchline format (e.g. 'Me: ... / AI: ...' or a numbered "
                     "list of 4-5 short items). Clean humour, no insults, still professional "
                     "enough for LinkedIn. Keep it under 700 characters.",
            "TIP": "Write a practical, correct tip a beginner can use today. Include a tiny "
                   "concrete example (a command or one line of code) written in plain text. "
                   "Only state things you are sure are true.",
        }[mode]
        task = f"MODE: {mode}. Topic: {seed}.\n{extra}"

    r = call_groq(model, task)
    if not r.get("post"):
        return None
    out = {"mode": mode, "headline": r.get("headline", mode), "post": r["post"].strip(),
           "card_text": (r.get("card_text") or "").strip(),
           "source_url": r.get("source_url", ""), "card_author": None}
    if quote:
        out["card_text"], out["card_author"] = quote[0], quote[1]
    return out


def pick_modes():
    forced = (os.environ.get("FORCE_MODE") or "").upper()
    modes = list(WEIGHTS)
    pool = {m: w for m, w in WEIGHTS.items()}
    order = []
    if forced in WEIGHTS:
        order.append(forced)
        pool.pop(forced)
    while pool:
        m = random.choices(list(pool), weights=list(pool.values()))[0]
        order.append(m)
        pool.pop(m)
    return order  # try in this order until two succeed


# ---------------------------------------------------------------- image card
def _font(size):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def make_card(text, author=None):
    """1080x1080 PNG bytes, or None if Pillow is unavailable."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    W = H = 1080
    pad = 100
    img = Image.new("RGB", (W, H), (15, 23, 42))
    d = ImageDraw.Draw(img)
    d.rectangle([pad, 120, pad + 120, 130], fill=(56, 189, 248))
    footer = os.environ.get("CARD_FOOTER", "")

    def wrap(font):
        lines, cur = [], ""
        for w in text.split():
            t = (cur + " " + w).strip()
            if d.textlength(t, font=font) <= W - 2 * pad:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines

    for size in range(84, 35, -4):
        font = _font(size)
        lines = wrap(font)
        if len(lines) * size * 1.35 <= 640:
            break
    y = 190
    for ln in lines:
        d.text((pad, y), ln, font=font, fill=(241, 245, 249))
        y += int(size * 1.35)
    if author:
        d.text((pad, y + 30), f"- {author}", font=_font(40), fill=(56, 189, 248))
    if footer:
        d.text((pad, H - 120), footer, font=_font(34), fill=(148, 163, 184))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# --------------------------------------------------------------------- email
def build_email(posts):
    subject = f"[{posts[0]['mode']}] {posts[0]['headline']}"[:120]
    lines = [f"Your LinkedIn drafts for {TODAY:%A, %d %B %Y}", "=" * 50, ""]
    for i, p in enumerate(posts):
        label = "PRIMARY" if i == 0 else "BACKUP"
        lines += [f"### {label} ({p['mode']}): {p['headline']}", "-" * 50, p["post"], ""]
        if p.get("card"):
            lines.append("Image card attached: upload it with the post.")
        if p["source_url"]:
            lines.append("Fact-check the source before posting.")
        lines += ["", ""]
    return subject, "\n".join(lines)


def send_email(subject, body, posts):
    user = os.environ["EMAIL_USER"]
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, user
    msg["To"] = os.environ.get("EMAIL_TO") or user
    msg.set_content(body)
    for i, p in enumerate(posts):
        if p.get("card"):
            msg.add_attachment(p["card"], maintype="image", subtype="png",
                               filename=f"{i + 1}_{p['mode'].lower()}_card.png")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, os.environ["EMAIL_APP_PASSWORD"])
        s.send_message(msg)


def main():
    model = resolve_model()
    print("Using Groq model:", model)
    posts = []
    for mode in pick_modes():
        if len(posts) == 2:
            break
        try:
            p = write_post(mode, model)
        except Exception as ex:
            print(f"[warn] {mode} failed: {ex}", file=sys.stderr)
            continue
        if not p:
            print(f"[info] {mode}: nothing good today, trying another mode")
            continue
        if mode in NEEDS_CARD and p["card_text"]:
            p["card"] = make_card(p["card_text"], p["card_author"])
        posts.append(p)
    if not posts:
        sys.exit("No post could be generated")

    subject, body = build_email(posts)
    if DRY_RUN:
        print(subject, "\n", body)
        for i, p in enumerate(posts):
            if p.get("card"):
                with open(f"dryrun_{i + 1}_{p['mode'].lower()}.png", "wb") as f:
                    f.write(p["card"])
    else:
        send_email(subject, body, posts)
        print("Email sent:", subject, "| modes:", [p["mode"] for p in posts])


if __name__ == "__main__":
    main()
