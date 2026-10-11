#!/usr/bin/env python3
"""Auto blog generator: Google Trends (India) -> AI article -> static SEO site.
Zero pip dependencies. Run by GitHub Actions; Cloudflare Pages deploys on push."""
import os, re, json, time, html, hashlib, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


SITE = env("SITE_URL", "https://smart-article-portal.pages.dev").rstrip("/")
SITE_NAME = env("SITE_NAME", "Smart Article Portal")
GEO = env("TRENDS_GEO", "IN")
N = int(env("POSTS_PER_RUN", "5") or 5)
ADSENSE = env("ADSENSE_ID")            # e.g. ca-pub-1234567890123456
CONTACT = env("CONTACT_EMAIL", "contact@example.com")
GSC_TOKEN = env("GSC_TOKEN", "X98iXRJTXyClV9iX9PgKRA6gjnxs7yhAZgJe5gHQtgg")
GROQ_KEY = env("GROQ_API_KEY")
GEMINI_KEY = env("GEMINI_API_KEY")
FT_KEY = env("FREETHEAI_API_KEY")

SKIP = re.compile(
    r"\b(dies|died|death|dead|killed|murder|rape|suicide|porn|nude|sex|"
    r"betting|casino|lottery|blast|riot|terror\w*|shot dead)\b", re.I)


# ---------------------------------------------------------------- helpers
def http(url, data=None, headers=None, timeout=90):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def local(e):
    return e.tag.split("}")[-1]


def ctext(e, name):
    for c in e:
        if local(c) == name:
            return (c.text or "").strip()
    return ""


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:70].strip("-")


def esc(s):
    return html.escape(s or "", quote=True)


def clean_html(s):
    s = re.sub(r"(?is)<(script|style|iframe|object|embed)[^>]*>.*?</\1>", "", s)
    s = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", s)
    s = re.sub(r"(?i)</?(html|head|body|h1)[^>]*>", "", s)
    return s.strip()


# ---------------------------------------------------------------- trends
def fetch_trends():
    urls = [
        f"https://trends.google.com/trending/rss?geo={GEO}",
        f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={GEO}",
    ]
    for u in urls:
        try:
            root = ET.fromstring(http(u))
        except Exception as e:
            print("trends fetch failed:", u, e)
            continue
        out = []
        for item in root.iter("item"):
            news = []
            for c in item:
                if local(c) == "news_item":
                    news.append({
                        "title": ctext(c, "news_item_title"),
                        "snippet": ctext(c, "news_item_snippet"),
                        "url": ctext(c, "news_item_url"),
                        "source": ctext(c, "news_item_source"),
                    })
            out.append({
                "title": (item.findtext("title") or "").strip(),
                "traffic": ctext(item, "approx_traffic"),
                "news": news,
            })
        if out:
            print(f"{len(out)} trends from {u}")
            return out
    return []


# ---------------------------------------------------------------- LLM
_MODELS = []
_IDX = 0


def gemini_models():
    """Ordered list of usable Gemini models for this key (best first)."""
    global _MODELS
    if _MODELS:
        return _MODELS
    forced = env("GEMINI_MODEL")
    if forced:
        _MODELS = [forced]
        return _MODELS
    data = json.loads(http(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
        headers={"x-goog-api-key": GEMINI_KEY}))
    names = [m["name"].split("/")[-1] for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    names = [n for n in names if n.startswith("gemini")]
    print("Available models:", ", ".join(names))

    def rank(n):
        r = 0
        if "flash" in n:
            r -= 10
        if "lite" in n:
            r -= 2
        if n.endswith("-latest"):
            r += 1
        if any(x in n for x in ("preview", "exp", "image", "tts", "audio", "live",
                                "thinking", "robotics", "computer", "embedding",
                                "pro", "gemma", "nano", "veo", "imagen", "omni", "vision")):
            r += 20
        return r

    names.sort(reverse=True)
    names.sort(key=rank)
    _MODELS = [n for n in names if rank(n) < 15][:15]
    if not _MODELS:
        raise RuntimeError("No Gemini models available for this key")
    print("Model order:", ", ".join(_MODELS))
    return _MODELS


def call_gemini(prompt):
    model = gemini_models()[min(_IDX, len(gemini_models()) - 1)]
    print("Trying model:", model)
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.6,
                                 "responseMimeType": "application/json"}}
    out = http(url, json.dumps(body).encode(),
               {"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY})
    return json.loads(out)["candidates"][0]["content"]["parts"][0]["text"]


def groq_models():
    """Usable Groq text models for this key, best first."""
    global _MODELS
    if _MODELS:
        return _MODELS
    forced = env("GROQ_MODEL")
    if forced:
        _MODELS = [forced]
        return _MODELS
    data = json.loads(http("https://api.groq.com/openai/v1/models",
                           headers={"Authorization": f"Bearer {GROQ_KEY}"}))
    ids = [m["id"] for m in data.get("data", []) if m.get("active", True)]
    print("Groq models:", ", ".join(ids))
    bad = ("whisper", "guard", "tts", "playai", "orpheus", "distil", "embed", "safeguard")
    ids = [i for i in ids if not any(b in i for b in bad)]

    def rank(i):
        r = 0
        if i == "llama-3.3-70b-versatile":
            r -= 30
        if "70b" in i:
            r -= 10
        if "versatile" in i:
            r -= 5
        if "gpt-oss-120b" in i:
            r -= 8
        elif "gpt-oss" in i:
            r -= 4
        if "8b" in i or "instant" in i:
            r += 5
        if "preview" in i or "vision" in i:
            r += 10
        return r

    ids.sort(key=rank)
    _MODELS = ids[:8]
    if not _MODELS:
        raise RuntimeError("No Groq models available for this key")
    print("Groq model order:", ", ".join(_MODELS))
    return _MODELS


def call_groq(prompt):
    ms = groq_models()
    model = ms[min(_IDX, len(ms) - 1)]
    print("Trying Groq model:", model)
    body = {"model": model,
            "messages": [{"role": "system", "content": "You output only valid JSON."},
                         {"role": "user", "content": prompt}],
            "temperature": 0.6, "max_tokens": 4000,
            "response_format": {"type": "json_object"}}
    out = http("https://api.groq.com/openai/v1/chat/completions",
               json.dumps(body).encode(),
               {"Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_KEY}"})
    return json.loads(out)["choices"][0]["message"]["content"]


def call_freetheai(prompt):
    body = {"model": env("FREETHEAI_MODEL", "free"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.6}
    out = http("https://api.freetheai.org/v1/chat/completions",
               json.dumps(body).encode(),
               {"Content-Type": "application/json",
                "Authorization": f"Bearer {FT_KEY}"})
    return json.loads(out)["choices"][0]["message"]["content"]


def ask_llm(prompt):
    global _IDX
    last = None
    for attempt in range(30):
        try:
            if GROQ_KEY:
                raw = call_groq(prompt)
            elif GEMINI_KEY:
                raw = call_gemini(prompt)
            elif FT_KEY:
                raw = call_freetheai(prompt)
            else:
                raise SystemExit("Set GROQ_API_KEY (or GEMINI_API_KEY) secret")
            a, b = raw.find("{"), raw.rfind("}")
            return json.loads(raw[a:b + 1], strict=False)
        except urllib.error.HTTPError as e:
            last = e
            try:
                msg = e.read().decode("utf-8", "replace").replace("\n", " ")
            except Exception:
                msg = ""
            print("HTTP", e.code, "->", msg[:250])
            # model not usable on this key (no access / not found / zero free quota)
            dead = (e.code in (403, 404)
                    or (e.code == 429 and "limit: 0" in msg)
                    or (e.code == 400 and "json_validate_failed" not in msg))
            if (GROQ_KEY or GEMINI_KEY) and dead:
                if _IDX < len(_MODELS) - 1:
                    _IDX += 1
                    continue
                raise RuntimeError("No usable model left on this key")
            time.sleep(35 if e.code == 429 else 8)
        except SystemExit:
            raise
        except RuntimeError:
            raise
        except Exception as e:
            last = e
            print("LLM error:", e)
            time.sleep(5)
    raise RuntimeError(f"LLM failed: {last}")


def write_article(t):
    today = datetime.now(IST).strftime("%d %B %Y")
    facts = "\n".join(
        f"- {n['title']} ({n['source']}): {n['snippet']}" for n in t["news"][:5]
    ) or "- (no headlines available)"
    prompt = f"""You are an experienced Indian news-explainer blogger.
Today is {today}. Topic currently trending on Google Trends ({GEO}): "{t['title']}" (approx searches: {t['traffic'] or 'n/a'}).
Reported headlines:
{facts}

Write an original, genuinely helpful, human-sounding article in English (700-900 words).
Rules:
- Use only the facts above plus well-established background. Never invent quotes, numbers, dates or scores. If unsure, say "reports suggest".
- Neutral tone. No medical, legal or financial advice. No clickbait. Do not mention being an AI.
- Body HTML allowed tags only: h2, h3, p, ul, li, strong, em. No h1.
- Sections: short intro paragraph, What happened, Why it is trending, Key facts (bullet list), What to watch next, FAQ (3 questions as h3 + p).
Return ONLY JSON with keys:
"title" (max 65 chars, includes main keyword), "description" (max 150 chars), "keywords" (array of 5 strings), "category" (one of: News, Sports, Entertainment, Business, Technology, Lifestyle, Education), "html" (article body)."""
    a = ask_llm(prompt)
    for k in ("title", "description", "html"):
        if not a.get(k):
            raise RuntimeError("missing field " + k)
    a["html"] = clean_html(a["html"])
    if len(re.sub(r"<[^>]+>", " ", a["html"]).split()) < 350:
        raise RuntimeError("article too short")
    return a


# ---------------------------------------------------------------- templates
CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--ac:#0b5fff;--card:#f6f7f9;--bd:#e3e5e8}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#9aa;--ac:#6ea0ff;--card:#1b1b1d;--bd:#2c2c30}}
*{box-sizing:border-box}body{margin:0;font:17px/1.7 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--ac)}header,footer{padding:14px 18px;border-bottom:1px solid var(--bd)}footer{border:0;border-top:1px solid var(--bd);margin-top:40px;color:var(--mut);font-size:14px}
header a{font-weight:700;font-size:20px;text-decoration:none}main{max-width:760px;margin:0 auto;padding:18px}
h1{line-height:1.25;font-size:28px}h2{margin-top:1.6em}.meta{color:var(--mut);font-size:14px}
.card{display:block;background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;margin:12px 0;text-decoration:none;color:inherit}
.card b{display:block;font-size:18px;color:var(--ac)}.card span{font-size:14px;color:var(--mut)}
.src li{font-size:14px}
"""


def page(title, desc, path, body, ld="", noindex=False):
    url = SITE + path
    ads = (f'<script async src="https://pagead2.googlesyndication.com/pagead/js/'
           f'adsbygoogle.js?client={esc(ADSENSE)}" crossorigin="anonymous"></script>'
           if ADSENSE else "")
    robots = '<meta name="robots" content="noindex">' if noindex else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">{robots}
<meta name="google-site-verification" content="{esc(GSC_TOKEN)}">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE_NAME)}" href="{SITE}/feed.xml">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<meta property="og:type" content="article"><meta property="og:url" content="{url}">
<meta name="twitter:card" content="summary">
<style>{CSS}</style>{ads}{ld}</head><body>
<header><a href="/">{esc(SITE_NAME)}</a></header>
<main>{body}</main>
<footer><a href="/about.html">About</a> · <a href="/privacy.html">Privacy Policy</a> · <a href="/disclaimer.html">Disclaimer</a> · <a href="/contact.html">Contact</a><br>&copy; {datetime.now(IST).year} {esc(SITE_NAME)}</footer>
</body></html>"""


def post_page(a, news):
    d = datetime.fromisoformat(a["date"]).astimezone(IST)
    ld = json.dumps({
        "@context": "https://schema.org", "@type": "BlogPosting",
        "headline": a["title"], "description": a["description"],
        "datePublished": a["date"], "dateModified": a["date"],
        "mainEntityOfPage": f"{SITE}/posts/{a['slug']}.html",
        "author": {"@type": "Organization", "name": SITE_NAME},
        "publisher": {"@type": "Organization", "name": SITE_NAME},
    })
    ld = f'<script type="application/ld+json">{ld}</script>'
    src = ""
    links = [n for n in news if n.get("url") and n.get("title")][:4]
    if links:
        src = "<h2>Related reports</h2><ul class='src'>" + "".join(
            f"<li><a href='{esc(n['url'])}' rel='nofollow noopener' target='_blank'>"
            f"{esc(n['title'])}</a> &mdash; {esc(n['source'])}</li>" for n in links
        ) + "</ul>"
    body = (f"<article><h1>{esc(a['title'])}</h1>"
            f"<p class='meta'>{esc(a['category'])} &middot; {d.strftime('%d %b %Y')}</p>"
            f"{a['html']}{src}</article>")
    return page(a["title"] + " | " + SITE_NAME, a["description"],
                f"/posts/{a['slug']}.html", body, ld)


def static_pages():
    pages = {
        "about.html": ("About", f"<h1>About {esc(SITE_NAME)}</h1><p>{esc(SITE_NAME)} explains the topics people in India are searching for right now, in plain language. Articles are prepared with the help of AI tools from publicly reported information and are updated as stories develop.</p><p>If you spot an error, please write to us so we can correct it.</p>"),
        "contact.html": ("Contact", f"<h1>Contact</h1><p>For corrections, feedback or business enquiries, email: <a href='mailto:{esc(CONTACT)}'>{esc(CONTACT)}</a></p>"),
        "disclaimer.html": ("Disclaimer", "<h1>Disclaimer</h1><p>The content on this website is for general information only. It is based on publicly available reports and may contain errors or become outdated. It is not professional, legal, medical or financial advice. Please verify important details from official sources.</p>"),
        "privacy.html": ("Privacy Policy", f"<h1>Privacy Policy</h1><p>We respect your privacy. This site does not require registration and we do not knowingly collect personal information directly.</p><h2>Cookies and advertising</h2><p>Third-party vendors, including Google, use cookies to serve ads based on your prior visits to this and other websites. Google's use of advertising cookies enables it and its partners to serve ads based on your visit to our site and/or other sites on the Internet. You may opt out of personalised advertising by visiting <a href='https://www.google.com/settings/ads' rel='nofollow'>Google Ads Settings</a>.</p><h2>Analytics and logs</h2><p>Our hosting provider may collect standard server logs such as IP address, browser type and pages visited, for security and performance.</p><h2>External links</h2><p>Articles may link to external websites. We are not responsible for their content or privacy practices.</p><h2>Contact</h2><p>Questions about this policy: <a href='mailto:{esc(CONTACT)}'>{esc(CONTACT)}</a></p>"),
    }
    for fn, (t, body) in pages.items():
        if not os.path.exists(fn):
            open(fn, "w", encoding="utf-8").write(
                page(f"{t} | {SITE_NAME}", f"{t} - {SITE_NAME}", "/" + fn, body))


def build_index(arts):
    cards = "".join(
        f"<a class='card' href='/posts/{esc(a['slug'])}.html'><b>{esc(a['title'])}</b>"
        f"<span>{esc(a['category'])} &middot; "
        f"{datetime.fromisoformat(a['date']).astimezone(IST).strftime('%d %b %Y')}</span>"
        f"<p style='margin:6px 0 0'>{esc(a['description'])}</p></a>"
        for a in arts[:60])
    body = f"<h1>{esc(SITE_NAME)}</h1><p class='meta'>Fresh explainers on what India is searching today.</p>{cards}"
    open("index.html", "w", encoding="utf-8").write(page(
        f"{SITE_NAME} - Trending topics explained",
        "Daily explainers on trending topics in India: news, sports, entertainment, business and technology.",
        "/", body))


def build_feeds(arts):
    urls = [(SITE + "/", None)] + [(f"{SITE}/{p}", None) for p in
            ("about.html", "contact.html", "privacy.html", "disclaimer.html")]
    urls += [(f"{SITE}/posts/{a['slug']}.html", a["date"][:10]) for a in arts]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u, d in urls:
        sm.append(f"<url><loc>{esc(u)}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>")
    sm.append("</urlset>")
    open("sitemap.xml", "w", encoding="utf-8").write("\n".join(sm))
    items = "".join(
        f"<item><title>{esc(a['title'])}</title><link>{SITE}/posts/{a['slug']}.html</link>"
        f"<guid>{SITE}/posts/{a['slug']}.html</guid><description>{esc(a['description'])}</description>"
        f"<pubDate>{datetime.fromisoformat(a['date']).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>"
        for a in arts[:30])
    open("feed.xml", "w", encoding="utf-8").write(
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{esc(SITE_NAME)}</title><link>{SITE}/</link>"
        f"<description>Trending topics explained</description>{items}</channel></rss>")
    open("robots.txt", "w").write(f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")


# ---------------------------------------------------------------- main
def load_articles():
    try:
        data = json.load(open("articles.json", encoding="utf-8"))
        return [a for a in data if isinstance(a, dict) and a.get("slug") and a.get("date")]
    except Exception:
        return []


def main():
    os.makedirs("posts", exist_ok=True)
    arts = load_articles()
    known = {a.get("topic", "").lower() for a in arts}
    made, fails = 0, 0
    for t in fetch_trends():
        if made >= N or fails >= 3:
            break
        topic = t["title"]
        blob = topic + " " + " ".join(n["title"] for n in t["news"])
        if not topic or topic.lower() in known or SKIP.search(blob):
            continue
        try:
            a = write_article(t)
        except Exception as e:
            fails += 1
            print("FAILED:", topic, "->", e)
            continue
        slug = slugify(a["title"]) or "trend-" + hashlib.md5(topic.encode()).hexdigest()[:8]
        if os.path.exists(f"posts/{slug}.html"):
            slug += "-" + hashlib.md5(topic.encode()).hexdigest()[:4]
        rec = {"slug": slug, "title": a["title"].strip(), "description": a["description"].strip(),
               "category": a.get("category", "News"), "topic": topic,
               "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "html": a["html"]}
        open(f"posts/{slug}.html", "w", encoding="utf-8").write(post_page(rec, t["news"]))
        rec.pop("html")
        arts.insert(0, rec)
        known.add(topic.lower())
        made += 1
        print("OK:", rec["title"])
        time.sleep(8)  # stay inside free-tier rate limits
    json.dump(arts, open("articles.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    static_pages()
    build_index(arts)
    build_feeds(arts)
    print(f"Done. {made} new posts, {len(arts)} total.")
    if made == 0 and fails > 0:
        raise SystemExit("No posts created - check the errors above")


if __name__ == "__main__":
    main()
