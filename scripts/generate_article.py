#!/usr/bin/env python3
"""Generate bilingual (EN/HI) articles with FreeTheAi and append them to articles.json.
Stdlib only. The API key is read from the FREETHEAI_API_KEY environment variable and never printed."""
import difflib, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

BASE = os.environ.get("FREETHEAI_BASE_URL", "https://api.freetheai.org/v1").rstrip("/")
KEY = os.environ.get("FREETHEAI_API_KEY", "").strip()
MODEL = os.environ.get("FREETHEAI_MODEL", "").strip()
try:
    COUNT = max(1, min(int(os.environ.get("ARTICLES_PER_RUN") or 1), 3))
except ValueError:
    COUNT = 1
DATA = "articles.json"
UA = "smart-article-portal/1.0 (+https://smart-article-portal.pages.dev)"

TOPICS = [
    ("Mining", "How iron ore is mined, graded and transported in Odisha: a general explainer"),
    ("Transport", "How to choose between tipper trucks and hyva trucks for bulk material hauling"),
    ("Construction", "Basics of civil contracting: tender stages, bid security and common contract terms in India"),
    ("Technology", "How small businesses can use AI tools safely and usefully"),
    ("Economy", "How to read basic Indian economic indicators such as GDP, inflation and repo rate"),
    ("Business", "How GST e-way bills work for goods transport in India"),
    ("Mining", "Coal grades in India and why grade matters for buyers: a general explainer"),
    ("Transport", "Fleet cost basics: fuel, tyres, maintenance and driver costs per trip"),
]
HEADS = ["title_en", "title_hi", "category", "description_en", "description_hi", "content_en", "content_hi", "tags"]
NOTE_EN = "\n\nNote: This is an AI-assisted general explainer, not news or professional advice. Verify specifics with official sources."
NOTE_HI = "\n\nनोट: यह AI की सहायता से तैयार किया गया सामान्य जानकारी वाला लेख है, समाचार या पेशेवर सलाह नहीं। विशेष तथ्य आधिकारिक स्रोतों से जाँच लें।"


def fail(msg, code=None):
    hints = {
        401: "Key rejected. Re-create the FREETHEAI_API_KEY secret.",
        403: "Forbidden. Check dashboard check-in, model name, key status.",
        404: "Not found. Check base URL and model name.",
        400: "Bad request. Model name is probably invalid.",
        429: "Quota or rate limit reached. Try later.",
    }
    print("ERROR:", msg.replace(KEY, "***") if KEY else msg)
    if code in hints:
        print("HINT:", hints[code])
    sys.exit(1)


def call(method, path, body=None, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    for n in range(1, retries + 1):
        req = urllib.request.Request(BASE + path, data=data, method=method, headers={
            "Authorization": "Bearer " + KEY, "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")[:400]
            if e.code in (429, 500, 502, 503, 504) and n < retries:
                time.sleep(15 * n)
                continue
            fail(f"HTTP {e.code} on {method} {path}: {raw}", e.code)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if n < retries:
                time.sleep(15 * n)
                continue
            fail(f"Network/parse error on {path}: {e}")


def pick_model():
    if MODEL:
        return MODEL
    ids = [m.get("id") for m in call("GET", "/models").get("data", []) if m.get("id")]
    if not ids:
        fail("No models returned by /models for this key.")
    print("Models available (first 25):", ", ".join(ids[:25]))
    print("Using first model. To choose one, set repository variable FREETHEAI_MODEL.")
    return ids[0]


def load():
    if not os.path.exists(DATA):
        return {"articles": []}, []
    try:
        raw = json.load(open(DATA, encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"{DATA} is invalid JSON; refusing to overwrite: {e}")
    if isinstance(raw, list):
        return raw, raw
    if isinstance(raw, dict) and isinstance(raw.get("articles"), list):
        return raw, raw["articles"]
    fail(f"{DATA} has an unexpected structure; refusing to overwrite.")


def save(raw):
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, DATA)


def norm(s):
    return re.sub(r"\W+", " ", str(s).lower()).strip()


def is_dup(a, items):
    t, b = norm(a["title_en"]), norm(a["content_en"])[:1500]
    for e in items:
        et = norm(e.get("title_en") or e.get("title") or "")
        eb = norm(e.get("content_en") or e.get("content") or "")[:1500]
        if et and difflib.SequenceMatcher(None, t, et).ratio() > 0.8:
            return True
        if eb and difflib.SequenceMatcher(None, b, eb).ratio() > 0.7:
            return True
    return False


def check(a):
    for k in HEADS:
        if not a.get(k):
            return f"missing {k}"
    if not isinstance(a["tags"], list):
        return "tags not a list"
    if len(a["content_en"].split()) < 150:
        return "English content too short"
    if len(re.findall(r"[\u0900-\u097F]", a["content_hi"])) < 200:
        return "Hindi content too short or not Devanagari"
    return None


def generate(model, cat, topic, titles):
    system = ("You write careful, original, educational articles for an Indian audience. Reply with ONE JSON object only, "
              "no markdown fences, no commentary.")
    user = f"""Write an evergreen explainer article on: {topic}
Category: {cat}
JSON keys: title_en, title_hi, category, description_en (max 160 chars), description_hi (max 160 chars),
content_en (250-300 words, paragraphs separated by blank lines), content_hi (the same article in natural Hindi, Devanagari), tags (3-5 short strings).
Rules: no breaking news, no prices, no statistics, no tender or company claims, no invented sources or quotes.
Describe general knowledge only. Label any estimate as an estimate. Do not copy existing articles.
Avoid titles similar to: {json.dumps(titles[-25:], ensure_ascii=False)}"""
    r = call("POST", "/chat/completions", {
        "model": model, "temperature": 0.6, "max_tokens": 4500,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    try:
        text = r["choices"][0]["message"]["content"] or ""
        fr = r["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, "unexpected response shape"
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, j = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[i:j + 1], strict=False), None
    except (ValueError, TypeError):
        return None, f"not valid JSON (finish_reason={fr}, length={len(text)}, starts={text[:100]!r}, ends={text[-60:]!r})"


def main():
    if not KEY:
        fail("FREETHEAI_API_KEY is empty. Add it under Settings > Secrets and variables > Actions.")
    raw, items = load()
    before = len(items)
    model = pick_model()
    print(f"Base: {BASE} | model: {model} | target: {COUNT} article(s)")
    titles = [str(e.get("title_en") or e.get("title") or "") for e in items]
    for i in range(COUNT):
        cat, topic = TOPICS[(len(items) + i) % len(TOPICS)]
        for attempt in (1, 2):
            a, err = generate(model, cat, topic, titles)
            err = err or (check(a) if isinstance(a, dict) else "not an object")
            if not err and is_dup(a, items):
                err = "duplicate of an existing article"
            if not err:
                break
            print(f"Attempt {attempt} rejected: {err}")
            a = None
        if not a:
            continue
        now = datetime.now(timezone.utc)
        slug = re.sub(r"[^a-z0-9]+", "-", a["title_en"].lower()).strip("-")[:60] or "article"
        items.append({
            "id": f"{slug}-{now:%Y%m%d%H%M%S}", "title_en": a["title_en"].strip(), "title_hi": a["title_hi"].strip(),
            "category": cat, "description_en": a["description_en"].strip(), "description_hi": a["description_hi"].strip(),
            "content_en": a["content_en"].strip() + NOTE_EN, "content_hi": a["content_hi"].strip() + NOTE_HI,
            "tags": [str(t) for t in a["tags"]][:6], "sources": [], "ai_generated": True,
            "published_at": now.isoformat(timespec="seconds"), "status": "published"})
        titles.append(a["title_en"])
        save(raw)
        print("Added:", a["title_en"])
    added = len(items) - before
    print(f"Done. Added {added}; total {len(items)}.")
    if added == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
