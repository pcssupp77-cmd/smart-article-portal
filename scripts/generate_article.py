#!/usr/bin/env python3
"""Generate bilingual (EN/HI) articles with FreeTheAi and append them to articles.json.
Stdlib only. The API key is read from the FREETHEAI_API_KEY environment variable and never printed."""
import difflib, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

BASE = os.environ.get("FREETHEAI_BASE_URL", "https://api.freetheai.xyz/v1").rstrip("/")
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
