#!/usr/bin/env python3
"""Daily AI -> LinkedIn draft emailer.

1. Collects fresh AI items (Hacker News, Reddit r/LocalLLaMA, HF daily papers)
   plus "on this day" tech history (Wikipedia).
2. Asks Groq to pick the most relatable story and write LinkedIn drafts.
3. Emails the drafts to you. You copy-paste and post. Stdlib only, no pip.

Env vars:
  GROQ_API_KEY        (required)
  EMAIL_USER          sender address (Gmail)           (required unless DRY_RUN)
  EMAIL_APP_PASSWORD  Gmail app password               (required unless DRY_RUN)
  EMAIL_TO            recipient, defaults to EMAIL_USER
  GROQ_MODEL          default llama-3.3-70b-versatile
  DRY_RUN=1           print instead of emailing
"""
import datetime as dt
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

MODEL = os.environ.get("GROQ_MODEL")  # optional override; auto-picked if unset
PREFERRED = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b",
             "llama-3.1-8b-instant"]
NOT_CHAT = ("whisper", "guard", "tts", "orpheus", "embed", "prompt-guard", "safeguard")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
UA = "daily-ai-post-bot/1.0"


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------- collectors
def from_hn():
    since = int(time.time()) - 36 * 3600
    out = {}
    for q in ["LLM", "open source model", "local inference", "AI agent", "GPU quantization"]:
        flt = urllib.parse.quote(f"created_at_i>{since},points>20")
        url = (f"https://hn.algolia.com/api/v1/search?query={urllib.parse.quote(q)}"
               f"&tags=story&numericFilters={flt}&hitsPerPage=10")
        for h in get_json(url).get("hits", []):
            out[h["objectID"]] = {
                "source": "HackerNews",
                "title": h.get("title", ""),
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                "score": h.get("points", 0),
                "note": "",
            }
    return list(out.values())


def from_hf_models():
    out = []
    for m in get_json("https://huggingface.co/api/models?sort=trendingScore&limit=12"):
        out.append({
            "source": "HF trending model",
            "title": m.get("id", ""),
            "url": f"https://huggingface.co/{m.get('id', '')}",
            "score": m.get("likes", 0),
            "note": f"task={m.get('pipeline_tag')}, downloads={m.get('downloads')}",
        })
    return out


def from_hf_papers():
    out = []
    for e in get_json("https://huggingface.co/api/daily_papers")[:12]:
        p = e.get("paper", {})
        out.append({
            "source": "HF Papers",
            "title": p.get("title") or e.get("title", ""),
            "url": f"https://huggingface.co/papers/{p.get('id', '')}",
            "score": p.get("upvotes", 0),
            "note": (p.get("summary") or "")[:250].replace("\n", " "),
        })
    return out


def from_instagram():
    """Optional: Google Programmable Search restricted to instagram.com."""
    key, cx = os.environ.get("GOOGLE_API_KEY"), os.environ.get("GOOGLE_CSE_ID")
    if not (key and cx):
        return []
    out = {}
    for q in ["AI model runs locally", "new open source AI model", "AI tool developers", "LLM breakthrough"]:
        url = ("https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
            {"key": key, "cx": cx, "q": q, "dateRestrict": "d2", "num": 5}))
        for it in get_json(url).get("items", []):
            imgs = it.get("pagemap", {}).get("cse_image", [])
            out[it["link"]] = {
                "source": "Instagram",
                "title": it.get("title", "")[:150],
                "url": it["link"],
                "score": 40,  # no like counts available; mild boost so it competes
                "note": it.get("snippet", "")[:250],
                "image": imgs[0].get("src", "") if imgs else "",
            }
    return list(out.values())


TECH_WORDS = ("comput", "software", "internet", "robot", "artificial", "chess", "program",
              "turing", "microsoft", "apple", "google", "intel", "ibm", "linux", "web",
              "satellite", "nasa", "technolog", "invent", "microprocessor", "network",
              "email", "browser", "neural", "algorithm", "supercomputer", "gpu", "openai")


def from_history():
    today = dt.date.today()
    url = ("https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/"
           f"{today.month:02d}/{today.day:02d}")
    d = get_json(url)
    out = []
    for kind in ("events", "births", "deaths"):
        for e in d.get(kind, []):
            text = e.get("text", "")
            if any(w in text.lower() for w in TECH_WORDS):
                page = (e.get("pages") or [{}])[0]
                out.append({
                    "kind": kind[:-1],
                    "year": e.get("year"),
                    "text": text[:220],
                    "url": page.get("content_urls", {}).get("desktop", {}).get("page", ""),
                })
    return out[:25]


def safe(fn):
    try:
        return fn()
    except Exception as ex:  # one dead source must not kill the run
        print(f"[warn] {fn.__name__} failed: {ex}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------- groq
SYSTEM = """You write LinkedIn drafts for a junior developer / CS student whose audience is
students and early-career engineers. They do NOT care about corporate gossip: valuations,
executive hires, funding rounds, lawsuits, "company X partnered with Y".
They DO care about things that make them say "wait, that's possible?": models running on
tiny hardware, big efficiency wins, new open-weight models, tools they can try today,
clear research results explained simply.

HARD RULES
- Use ONLY facts present in the provided items. Never invent numbers, names or claims.
  If an item is vague, say what is known and skip what isn't.
- Plain English. Explain any jargon in half a sentence. No hype words (revolutionary, game-changer).
- LinkedIn format: 1 strong hook line, blank line, 3-5 short lines/bullets, one takeaway line,
  one genuine question to the reader, then 3-4 hashtags. 900-1300 characters max. Max 2 emojis.
- Include the source URL on its own line near the end.
- Categories for the news post: DEV_WIN (cheaper/faster/local/open model or tool),
  RESEARCH_SIMPLIFIED (a paper with a clear result), FUN (weird or amusing AI story).
- Reject items that are corporate/political news. If nothing is good, set news_post to null
  and news_strength to 1-3.

Return ONLY JSON:
{"news_strength": <1-10, how impressive/relatable the best item is>,
 "news_post": {"category": "...", "headline": "...", "source_url": "...", "post": "..."} or null,
 "history_post": {"year": <int>, "headline": "...", "source_url": "...", "post": "..."} or null}
For history_post pick the most interesting tech/computing/AI-related "on this day" item
(prefer one that connects to today's AI world) and write it as a fun 'On this day' post."""


def resolve_model():
    """Pick a chat model this key can actually use (Groq retires models often)."""
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


def call_groq(user_msg, model):
    body = json.dumps({
        "model": model,
        "temperature": 0.6,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user_msg}],
    }).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
                 "Content-Type": "application/json", "User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(json.load(r)["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            raise RuntimeError(f"Groq error {e.code}: {e.read().decode()[:300]}")


# --------------------------------------------------------------------- email
def build_email(result, today, images=None):
    images = images or {}
    strength = result.get("news_strength", 0) or 0
    news, hist = result.get("news_post"), result.get("history_post")
    ordered = []
    if news and strength >= 6:
        ordered = [("PRIMARY - AI news", news), ("BACKUP - this day in tech", hist)]
    else:
        ordered = [("PRIMARY - quiet news day, fun one", hist), ("BACKUP - weak news pick", news)]
    ordered = [(label, p) for label, p in ordered if p]
    if not ordered:
        raise RuntimeError("Groq returned no usable posts")

    lines = [f"Your LinkedIn drafts for {today:%A, %d %B %Y}", "=" * 50, ""]
    for label, p in ordered:
        lines += [f"### {label}: {p.get('headline', '')}", "-" * 50,
                  p.get("post", "").strip(), "",
                  f"Source: {p.get('source_url', 'n/a')}",
                  *([f"Image: {images[p['source_url']]}  (credit the original creator)"]
                    if p.get("source_url") in images else []), "",
                  "(Quick fact-check the source before posting.)", "", ""]
    subject = f"LinkedIn draft ready: {ordered[0][1].get('headline', 'today')}"[:120]
    return subject, "\n".join(lines)


def send_email(subject, body):
    user = os.environ["EMAIL_USER"]
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, user
    msg["To"] = os.environ.get("EMAIL_TO") or user
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, os.environ["EMAIL_APP_PASSWORD"])
        s.send_message(msg)


def main():
    today = dt.date.today()
    news = safe(from_hn) + safe(from_hf_models) + safe(from_hf_papers) + safe(from_instagram)
    news.sort(key=lambda x: x.get("score", 0), reverse=True)
    news = news[:30]
    images = {n["url"]: n["image"] for n in news if n.get("image")}
    # keep the prompt small: the model doesn't need image URLs
    news = [{k: v for k, v in n.items() if k != "image"} for n in news]
    history = safe(from_history)
    if not news and not history:
        sys.exit("No source returned anything")

    user_msg = (f"Today is {today:%d %B %Y}.\n\nNEWS ITEMS (last ~24h):\n"
                + json.dumps(news, ensure_ascii=False)
                + "\n\nON THIS DAY CANDIDATES:\n" + json.dumps(history, ensure_ascii=False))
    model = resolve_model()
    print("Using Groq model:", model)
    result = call_groq(user_msg, model)
    subject, body = build_email(result, today, images)

    if DRY_RUN:
        print(subject, "\n", body)
    else:
        send_email(subject, body)
        print("Email sent:", subject)


if __name__ == "__main__":
    main()
