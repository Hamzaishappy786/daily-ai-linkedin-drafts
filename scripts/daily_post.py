#!/usr/bin/env python3
"""Daily LinkedIn draft emailer.

Every run picks random content "modes" (news, tip, quote, funny, motivation, tech history),
has Groq write a LinkedIn post for each, grabs a REAL image from the source where one
exists, and emails you the best two. You copy-paste and post. Stdlib only.

Env vars:
  GROQ_API_KEY        (required)
  EMAIL_USER, EMAIL_APP_PASSWORD   Gmail sender + app password (unless DRY_RUN)
  EMAIL_TO            recipient, defaults to EMAIL_USER
  GROQ_MODEL          optional model override (auto-picked otherwise)
  FORCE_MODE          optional: NEWS|TIP|QUOTE|FUNNY|MOTIVATION|HISTORY (for testing)
  DRY_RUN=1           print instead of emailing
"""
import datetime as dt
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.message import EmailMessage

MODEL = os.environ.get("GROQ_MODEL")
PREFERRED = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b",
             "llama-3.1-8b-instant"]
NOT_CHAT = ("whisper", "guard", "tts", "orpheus", "embed", "safeguard")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
UA = "daily-ai-post-bot/3.0"
BROWSER_UA = "Mozilla/5.0 (compatible; daily-ai-post-bot/3.0)"
TODAY = dt.date.today()
DAY = TODAY.toordinal()
try:  # how many drafts per email (default 2; set via the manual "Run workflow" form)
    POST_COUNT = max(1, min(6, int(os.environ.get("POST_COUNT") or 2)))
except ValueError:
    POST_COUNT = 2

# ------------------------------------------------------------- static content
# (quote, author, Wikipedia page for the author's photo or None).
# Real, well-known quotes. The model never invents a quote; it only writes around these.
QUOTES = [  # mix of general work/life quotes (most) and a few tech ones
    ("The only way to do great work is to love what you do.", "Steve Jobs", "Steve_Jobs"),
    ("Talk is cheap. Show me the code.", "Linus Torvalds", "Linus_Torvalds"),
    ("You miss 100% of the shots you don't take.", "Wayne Gretzky", "Wayne_Gretzky"),
    ("Well done is better than well said.", "Benjamin Franklin", "Benjamin_Franklin"),
    ("A ship in port is safe, but that's not what ships are built for.", "Grace Hopper",
     "Grace_Hopper"),
    ("We are what we repeatedly do. Excellence, then, is not an act, but a habit.",
     "Will Durant", "Will_Durant"),
    ("Genius is one percent inspiration, ninety-nine percent perspiration.",
     "Thomas Edison", "Thomas_Edison"),
    ("The best way to predict the future is to invent it.", "Alan Kay", "Alan_Kay"),
    ("I have no special talent. I am only passionately curious.", "Albert Einstein",
     "Albert_Einstein"),
    ("If you want to go fast, go alone. If you want to go far, go together.",
     "African proverb", None),
    ("We can only see a short distance ahead, but we can see plenty there that needs to be done.",
     "Alan Turing", "Alan_Turing"),
    ("The best way out is always through.", "Robert Frost", "Robert_Frost"),
    ("Fall seven times, stand up eight.", "Japanese proverb", None),
    ("The real problem is not whether machines think but whether men do.", "B. F. Skinner",
     "B._F._Skinner"),
    ("A smooth sea never made a skilled sailor.", "English proverb", None),
    ("The Analytical Engine has no pretensions whatever to originate anything. It can do "
     "whatever we know how to order it to perform.", "Ada Lovelace, 1843", "Ada_Lovelace"),
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
        "a prompting trick to get better answers out of ChatGPT-style tools",
        "rubber duck debugging: explain the problem out loud",
        "reading an error message from the bottom up",
        "how to run a small AI model on your own laptop with Ollama",
        "a free resource for learning how AI actually works",
        "a GitHub profile README that recruiters actually notice",
        "writing a good commit message",
        "a Python standard-library module most beginners miss",
        "how to ask a technical question so people actually answer",
    ],
}
WEIGHTS = {"NEWS": 24, "TIP": 18, "QUOTE": 16, "FUNNY": 16, "MOTIVATION": 14, "HISTORY": 12}


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
    for q in ["AI", "LLM", "open source model", "run locally"]:
        flt = urllib.parse.quote(f"created_at_i>{since},points>50")
        url = (f"https://hn.algolia.com/api/v1/search?query={urllib.parse.quote(q)}"
               f"&tags=story&numericFilters={flt}&hitsPerPage=8")
        for h in get_json(url).get("hits", []):
            out[h["objectID"]] = {
                "source": "HackerNews", "title": h.get("title", ""),
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                "score": h.get("points", 0), "note": ""}
    return sorted(out.values(), key=lambda x: -x["score"])[:10]


def from_hf_models():
    return [{"source": "HF trending model", "title": m.get("id", ""),
             "url": f"https://huggingface.co/{m.get('id', '')}", "score": m.get("likes", 0),
             "note": f"task={m.get('pipeline_tag')}, downloads={m.get('downloads')}"}
            for m in get_json("https://huggingface.co/api/models?sort=trendingScore&limit=5")]


def from_hf_papers():
    out = []
    for e in get_json("https://huggingface.co/api/daily_papers")[:5]:
        p = e.get("paper", {})
        out.append({"source": "HF Papers", "title": p.get("title") or e.get("title", ""),
                    "url": f"https://huggingface.co/papers/{p.get('id', '')}",
                    "score": p.get("upvotes", 0),
                    "note": (p.get("summary") or "")[:250].replace("\n", " ")})
    return out


def from_github_new_repos():
    since = (TODAY - dt.timedelta(days=7)).isoformat()
    q = urllib.parse.quote(f"topic:llm created:>{since} stars:>100")
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    d = get_json(f"https://api.github.com/search/repositories?q={q}&sort=stars&per_page=5",
                 headers=headers)
    return [{"source": "GitHub new repo", "title": r["full_name"], "url": r["html_url"],
             "score": r["stargazers_count"],
             "note": (r.get("description") or "")[:250]} for r in d.get("items", [])]


RSS_FEEDS = [
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("Ars Technica AI", "https://arstechnica.com/ai/feed/"),
    ("Hugging Face blog", "https://huggingface.co/blog/feed.xml"),
]
ATOM = "{http://www.w3.org/2005/Atom}"


def _strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def from_rss():
    out = []
    for name, url in RSS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                root = ET.fromstring(r.read())
        except Exception as ex:
            print(f"[warn] rss {name} failed: {ex}", file=sys.stderr)
            continue
        items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
        for it in items[:5]:
            link = it.findtext("link")
            if not link:
                el = it.find(f"{ATOM}link")
                link = el.get("href") if el is not None else ""
            desc = it.findtext("description") or it.findtext(f"{ATOM}summary") or ""
            out.append({"source": name,
                        "title": _strip(it.findtext("title") or it.findtext(f"{ATOM}title")),
                        "url": (link or "").strip(), "score": 0, "note": _strip(desc)[:250]})
    return out


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


# -------------------------------------------------------------------- images
def og_image(page_url):
    """The page's own preview image (what LinkedIn/Twitter show for the link)."""
    if not page_url:
        return None
    try:
        req = urllib.request.Request(page_url, headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            html = r.read(400_000).decode("utf-8", "replace")
    except Exception:
        return None
    for pat in (r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']'):
        m = re.search(pat, html, re.I)
        if m:
            return urllib.parse.urljoin(page_url, m.group(1).replace("&amp;", "&"))
    return None


def fetch_image(page_url):
    """Return dict(url, data, subtype, credit) or None. Only png/jpeg/gif, max 5 MB."""
    img_url = og_image(page_url)
    if not img_url:
        return None
    info = {"url": img_url, "data": None, "subtype": None,
            "credit": urllib.parse.urlparse(page_url).netloc.replace("www.", "")}
    try:
        req = urllib.request.Request(img_url, headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            ctype = r.headers.get_content_type()
            data = r.read(5_000_001)
        if ctype in ("image/png", "image/jpeg", "image/gif") and len(data) <= 5_000_000:
            info["data"], info["subtype"] = data, ctype.split("/")[1]
    except Exception:
        pass
    return info


# --------------------------------------------------------------------- groq
STYLE = """You ghostwrite LinkedIn posts for a CS student / junior developer who wants a
professional, interesting profile. Audience: students and early-career people who are
tech-curious but are NOT machine-learning specialists.

VOICE: friendly, confident, plain English, like a sharp friend explaining something cool.
Professional, never corporate or cringe. Short sentences. Explain anything technical in
everyday words.

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
          '"source_url": "<url or empty string>", '
          '"appeal": <number 1-10, NEWS mode only, else 0>}')


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
    last_err = None
    for attempt in range(4):
        strict = attempt < 3  # final attempt: no strict JSON mode, extract JSON ourselves
        payload = {
            "model": model, "temperature": temperature, "max_completion_tokens": 4096,
            "messages": [{"role": "system", "content": STYLE},
                         {"role": "user", "content": task + "\n\nReturn JSON: " + SCHEMA}],
        }
        if strict:
            payload["response_format"] = {"type": "json_object"}
        if "gpt-oss" in model:
            payload["reasoning_effort"] = "low"
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
                     "Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                text = json.load(r)["choices"][0]["message"]["content"] or ""
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start:end + 1])
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            last_err = f"Groq error {e.code}: {detail}"
            if e.code in (429, 500, 503):
                time.sleep(10 * (attempt + 1))
            elif e.code == 400 and "json_validate_failed" in detail:
                time.sleep(2)
            else:
                raise RuntimeError(last_err)
        except ValueError as e:  # empty or unparsable reply
            last_err = f"Bad JSON from model: {e}"
            time.sleep(2)
    raise RuntimeError(last_err)


# -------------------------------------------------------------- mode writers
_news_cache = {}


def get_news():
    if "n" not in _news_cache:
        _news_cache["n"] = (safe(from_rss) + safe(from_hn) + safe(from_hf_models)
                            + safe(from_hf_papers) + safe(from_github_new_repos))
    return _news_cache["n"]


def write_post(mode, model):
    """Return dict(mode, headline, post, source_url, image_page) or None."""
    quote = None
    image_page = ""
    if mode == "NEWS":
        news = get_news()
        if not news:
            return None
        task = (
            "MODE: NEWS. Pick ONE item that a tech-curious student (not an ML specialist) "
            "would understand in 5 seconds and find impressive or useful: something they can "
            "try today, a big drop in size/cost/speed shown in plain numbers (e.g. 'runs on a "
            "laptop'), a surprising thing AI just did, or something that changes which skills "
            "are worth learning.\n"
            "REJECT: corporate news (valuations, hires, funding, lawsuits, partnerships), "
            "code forks/patches/library internals, hardware-specific benchmarks, and anything "
            "whose headline needs jargon (quantization, inference, tokens, kernels) to "
            "understand. Explain it like to a friend who has never trained a model.\n"
            "Rate your pick with \"appeal\" 1-10 (10 = a non-technical friend says 'wow'). "
            "If your best pick is below 7, return an empty post.\n"
            "Structure: hook with the surprising fact; what happened (2-3 lines); why it "
            "matters to the reader (1-2 lines); source URL on its own line; question; "
            "hashtags.\n\nITEMS:\n" + json.dumps(news, ensure_ascii=False))
    elif mode == "HISTORY":
        hist = safe(from_history)
        if not hist:
            return None
        task = (
            f"MODE: HISTORY. Today is {TODAY:%d %B}. From these 'on this day' items pick the "
            "single most interesting tech/computing/AI one, ideally one that connects to "
            "today's AI world. Write a fun 'On this day' post. Mention how many years ago "
            f"it was (current year is {TODAY.year}). Use only the facts given. Put the "
            "source URL on its own line.\n\nITEMS:\n" + json.dumps(hist, ensure_ascii=False))
    elif mode == "QUOTE":
        quote = QUOTES[DAY % len(QUOTES)]
        image_page = f"https://en.wikipedia.org/wiki/{quote[2]}" if quote[2] else ""
        task = (
            f'MODE: QUOTE. Quote: "{quote[0]}" by {quote[1]}.\n'
            "Write a post that opens with the quote in quotation marks plus the author on "
            "the next line, then 3-4 short lines connecting it to everyday working life for "
            "ANY job or study (students, teachers, nurses, designers, managers, engineers, "
            "freelancers). Use a relatable workplace or learning moment. Only mention coding "
            "or AI if the quote itself is about technology. Do not claim the author said "
            "anything beyond this quote.")
    else:
        seed = SEEDS[mode][DAY % len(SEEDS[mode])]
        extra = {
            "MOTIVATION": "Write an honest motivational post for students and early-career "
                          "people. No fake success stories; use a universal observation or a "
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
    if mode == "NEWS":
        try:
            if float(r.get("appeal", 0)) < 7:
                print(f"[info] NEWS appeal too low ({r.get('appeal')}), skipping")
                return None
        except (TypeError, ValueError):
            return None
    src = r.get("source_url", "") or ""
    if mode in ("NEWS", "HISTORY"):
        image_page = src
    return {"mode": mode, "headline": r.get("headline", mode), "post": r["post"].strip(),
            "source_url": src, "image_page": image_page}


def pick_modes():
    forced = (os.environ.get("FORCE_MODE") or "").upper()
    pool = dict(WEIGHTS)
    order = []
    if forced in WEIGHTS:
        order.append(forced)
        pool.pop(forced)
    while pool:
        m = random.choices(list(pool), weights=list(pool.values()))[0]
        order.append(m)
        pool.pop(m)
    return order  # try in this order until two succeed


# --------------------------------------------------------------------- email
def build_email(posts):
    subject = f"[{posts[0]['mode']}] {posts[0]['headline']}"[:120]
    lines = [f"Your LinkedIn drafts for {TODAY:%A, %d %B %Y}", "=" * 50, ""]
    for i, p in enumerate(posts):
        label = "PRIMARY" if i == 0 else "BACKUP" if i == 1 else f"EXTRA {i - 1}"
        lines += [f"### {label} ({p['mode']}): {p['headline']}", "-" * 50, p["post"], ""]
        img = p.get("image")
        if img:
            note = "attached" if img["data"] else "not attachable, open the link"
            lines.append(f"Image ({note}): {img['url']}")
            lines.append(f"Credit: {img['credit']}. Tip: if your post includes the source "
                         "link, LinkedIn shows this preview image automatically.")
        if p["source_url"]:
            lines.append("Fact-check the source before posting.")
        lines += ["", ""]
    lines += ["-" * 50,
              "Want more drafts? GitHub repo -> Actions -> Daily AI LinkedIn draft -> "
              "Run workflow, then pick how many drafts (up to 6) and optionally a type "
              "(NEWS, TIP, QUOTE, FUNNY, MOTIVATION, HISTORY). A fresh email arrives in ~1 minute."]
    return subject, "\n".join(lines)


def send_email(subject, body, posts):
    user = os.environ["EMAIL_USER"]
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, user
    msg["To"] = os.environ.get("EMAIL_TO") or user
    msg.set_content(body)
    for i, p in enumerate(posts):
        img = p.get("image")
        if img and img["data"]:
            ext = "jpg" if img["subtype"] == "jpeg" else img["subtype"]
            msg.add_attachment(img["data"], maintype="image", subtype=img["subtype"],
                               filename=f"{i + 1}_{p['mode'].lower()}.{ext}")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, os.environ["EMAIL_APP_PASSWORD"])
        s.send_message(msg)


def main():
    model = resolve_model()
    print("Using Groq model:", model)
    posts = []
    for mode in pick_modes():
        if len(posts) >= POST_COUNT:
            break
        try:
            p = write_post(mode, model)
        except Exception as ex:
            print(f"[warn] {mode} failed: {ex}", file=sys.stderr)
            continue
        if not p:
            print(f"[info] {mode}: nothing good today, trying another mode")
            continue
        p["image"] = fetch_image(p["image_page"]) if p["image_page"] else None
        posts.append(p)
    if not posts:
        sys.exit("No post could be generated")

    subject, body = build_email(posts)
    if DRY_RUN:
        print(subject, "\n", body)
    else:
        send_email(subject, body, posts)
        print("Email sent:", subject, "| modes:", [p["mode"] for p in posts])


if __name__ == "__main__":
    main()
