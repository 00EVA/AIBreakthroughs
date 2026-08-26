#!/usr/bin/env python3
"""
breakthrough_probe.py - Breakthrough watcher for AIBreakthroughs.

Mirrors AIincidents' news_probe.py patterns (key-free sources, regex signal
gate, seen_urls.json dedupe) but auto-publishes: qualifying items become
entries in data/breakthroughs.json immediately.

Sources (all key-free):
  - arXiv RSS new listings: cs.AI, cs.CL, cs.LG
  - Hacker News (Algolia) - keyword search
  - Google News RSS - keyword search
  - Hugging Face daily papers
  - Tech/AI news feeds (The Register, Ars Technica, The Decoder,
    TechCrunch AI, VentureBeat AI, WIRED AI, MarkTechPost)

Quality gate before an item is promoted to a database entry:
  1. Must match BREAKTHROUGH_STRONG (real capability language)
  2. Recency window (default 14 days) unless from arXiv new-listings
  3. URL not already seen / not already an entry
Each promoted entry is stamped era=live-discovery, conservative scores
(impact/surprise <= 7, never gold-tier) and tags["auto"]=yes provenance.

After promotion the probe runs build_csv.py, then commits+pushes so
GitHub Pages (built from main/) refreshes automatically.

Usage: python3 monitor/breakthrough_probe.py [--limit N] [--dry-run]
Cron:  30 */2 * * * cd ~/AIBreakthroughs && python3 monitor/breakthrough_probe.py \
         >> ~/Library/Logs/aibreakthroughs_watch.log 2>&1
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SEEN = os.path.join(ROOT, "seen_urls.json")
DATA = os.path.join(ROOT, "data")
DBJSON = os.path.join(DATA, "breakthroughs.json")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AIBreakthroughsWatch/1.0"

# Strong capability language required for AUTO-PROMOTION into the dataset.
BREAKTHROUGH_STRONG = re.compile(
    r"(breakthrough|state[- ]of[- ]the[- ]art|\bsota\b|outperforms?|"
    r"surpass(?:es)?|sets? (?:a )?new record|first[- ]ever|for the first time|"
    r"beats? .{0,40}(?:gpt|gemini|claude|frontier|o3|o4)|achieves .{0,30}(?:record|superhuman)|"
    r"new (?:model|architecture) .{0,40}(?:match|beat|exceed)|"
    r"superhuman|human.level performance|passes? .{0,25}(?:exam|benchmark)|"
    r"open.?weight.{0,60}(?:release|publish)|gold.medal|nobel)",
    re.I,
)

SOFT_CONTEXT = re.compile(
    r"(model|llm|ai|agent|robot|network|transformer|diffusion|inference|"
    r"benchmark|training|gpu|chip|drug|diagnos|protein|material|fusion|quantum)",
    re.I,
)

RECENCY_DAYS = 14

ARXIV_FEEDS = [
    ("arXiv cs.AI", "http://export.arxiv.org/rss/cs.AI"),
    ("arXiv cs.CL", "http://export.arxiv.org/rss/cs.CL"),
    ("arXiv cs.LG", "http://export.arxiv.org/rss/cs.LG"),
]

HN_QUERIES = [
    "AI breakthrough",
    "state-of-the-art AI model",
    "open weight release",
    "beats GPT",
]

GN_QUERIES = [
    "AI breakthrough announcement",
    "new AI model outperforms state of the art",
    "AI sets record benchmark",
    "lab releases open weights model",
    "AI protein OR drug discovery milestone",
    "humanoid robot achieves first",
    "AI weather OR fusion OR materials discovery breakthrough",
]

FEEDS = [
    ("The Register", "https://www.theregister.com/headlines.atom"),
    ("Ars Technica", "https://arstechnica.com/feed/"),
    ("The Decoder", "https://the-decoder.com/feed/"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed"),
    ("WIRED AI", "https://www.wired.com/feed/tag/ai/latest/rss"),
    ("MarkTechPost", "https://marktechpost.com/feed/"),
]

HF_PAPERS = "https://huggingface.co/api/daily_papers"

ORG_PATTERNS = [
    (r"openai|gpt-oss", "OpenAI"),
    (r"deepmind|google", "Google DeepMind"),
    (r"anthropic|claude", "Anthropic"),
    (r"\bmeta\b|llama", "Meta AI"),
    (r"nvidia", "NVIDIA"),
    (r"deepseek", "DeepSeek"),
    (r"qwen|alibaba", "Alibaba Qwen"),
    (r"kimi|moonshot", "Moonshot AI"),
    (r"\bglm\b|zhipu|z\.ai", "Z.ai"),
    (r"\bmistral\b", "Mistral AI"),
    (r"\bxai\b|grok", "xAI"),
    (r"microsoft", "Microsoft"),
    (r"cerebras", "Cerebras"),
    (r"isomorphic", "Isomorphic Labs"),
    (r"\bwaymo\b", "Waymo"),
    (r"unitree|figure|optimus|tesla", "Humanoid robotics"),
]

CATEGORY_MAP = [
    (r"robot|humanoid|embodim|manipulat", "robotics"),
    (r"diagnos|drug|clinic|medic|patient|protein|therap", "ai-for-science"),
    (r"weather|climate|forecast|fusion|crystal|material|quantum", "ai-for-science"),
    (r"chip|gpu|inference silicon|wafer|lpu|tpu|hbm", "hardware-milestone"),
    (r"open.?weight|license|apache|mit license|weights released", "open-ecosystem"),
]


def load_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(p, data):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def fetch(url, timeout=15):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json,text/xml,*/*"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def within_window(datestr):
    d = (datestr or "")[:10]
    if not re.match(r"\d{4}-\d{2}-\d{2}", d):
        return True
    cutoff = (datetime.date.today() - datetime.timedelta(days=RECENCY_DAYS)).isoformat()
    return d >= cutoff


def collect():
    """Return raw candidate dicts {title,url,source,date,snippet}."""
    out = []

    for name, url in ARXIV_FEEDS:
        try:
            root = ET.fromstring(fetch(url))
            for e in root.findall("channel/item")[:40]:
                t = e.findtext("title", "") or ""
                # arXiv titles arrive newline-folded
                t = re.sub(r"\s+", " ", t).strip()
                link = (e.findtext("link", "") or "").strip()
                desc = re.sub(r"\s+", " ", e.findtext("description", "") or "")
                out.append({"title": t, "url": link, "source": name,
                            "date": (e.findtext("pubDate", "") or "")[:10],
                            "snippet": desc[:400]})
        except Exception as ex:
            print(f"[warn] {name} failed: {ex}")

    for q in HN_QUERIES[:2]:
        try:
            u = ("https://hn.algolia.com/api/v1/search_by_date"
                 f"?query={urllib.parse.quote(q)}&tags=story&hitsPerPage=15")
            for h in json.loads(fetch(u)).get("hits", []):
                created = (h.get("created_at") or "")[:10]
                if not within_window(created):
                    continue
                out.append({
                    "title": h.get("title") or "",
                    "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    "source": "Hacker News", "date": created,
                    "snippet": (h.get("story_text") or h.get("title") or "")[:300],
                })
        except Exception as ex:
            print(f"[warn] HN '{q}' failed: {ex}")

    for q in GN_QUERIES[:5]:
        try:
            u = ("https://news.google.com/rss/search?"
                 f"q={urllib.parse.quote(q)}&hl=en-US&gl=US&ceid=US:en")
            root = ET.fromstring(fetch(u))
            for e in root.findall("channel/item")[:8]:
                t = e.findtext("title", "") or ""
                lel = e.find("link")
                link = lel.text if lel is not None else ""
                if "news.google.com/rss/articles/" in link:
                    link = urllib.parse.urlsplit(link)._replace(query="").geturl()
                summ = re.sub(r"<[^>]+>", "", e.findtext("description", "") or "")
                d = (e.findtext("pubDate", "") or "")[:10]
                if not within_window(d):
                    continue
                out.append({"title": t, "url": link, "source": "Google News",
                            "date": d, "snippet": summ[:300]})
        except Exception as ex:
            print(f"[warn] Google News '{q}' failed: {ex}")

    try:
        for p in json.loads(fetch(HF_PAPERS))[:15]:
            paper = p.get("paper") or {}
            t = re.sub(r"\s+", " ", paper.get("title") or "").strip()
            d = (paper.get("publishedAt") or "")[:10]
            if not within_window(d):
                continue
            out.append({
                "title": t,
                "url": f"https://huggingface.co/papers/{paper.get('id', '')}",
                "source": "HF daily papers", "date": d,
                "snippet": (paper.get("summary") or "")[:300],
            })
    except Exception as ex:
        print(f"[warn] HF papers failed: {ex}")

    for name, url in FEEDS:
        try:
            root = ET.fromstring(fetch(url))
            entries = root.findall("{http://www.w3.org/2005/Atom}entry")
            atom = bool(entries)
            if not entries:
                entries = root.findall("channel/item")
            for e in entries[:12]:
                if atom:
                    t = e.findtext("{http://www.w3.org/2005/Atom}title", "") or ""
                    lel = e.find("{http://www.w3.org/2005/Atom}link")
                    link = lel.get("href") if lel is not None else ""
                    summ = (e.findtext("{http://www.w3.org/2005/Atom}summary", "")
                            or e.findtext("{http://www.w3.org/2005/Atom}content", "") or "")
                    d = (e.findtext("{http://www.w3.org/2005/Atom}updated", ""))[:10]
                else:
                    t = e.findtext("title", "") or ""
                    lel = e.find("link")
                    link = lel.text if lel is not None else ""
                    summ = e.findtext("description", "") or ""
                    d = (e.findtext("pubDate", "") or "")[:10]
                if not within_window(d):
                    continue
                out.append({"title": t, "url": link, "source": name,
                            "date": d, "snippet": summ[:300]})
        except Exception as ex:
            print(f"[warn] feed {name} failed: {ex}")

    return out


def slugify(t, maxlen=48):
    s = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return s[:maxlen].rstrip("-")


def guess_org(text):
    for pat, org in ORG_PATTERNS:
        if re.search(pat, text, re.I):
            return org
    return "Unattributed"


def guess_category(text):
    for pat, cat in CATEGORY_MAP:
        if re.search(pat, text, re.I):
            return cat
    return "capability-leap"


def score(item):
    """Conservative heuristic scoring - auto entries never reach gold tier."""
    blob = f"{item['title']} {item['snippet']}"
    impact, surprise = 6, 5
    if re.search(r"first[- ]ever|for the first time|superhuman", blob, re.I):
        impact += 1
    if re.search(r"state[- ]of[- ]the[- ]art|\bsota\b|outperforms?|surpass", blob, re.I):
        impact += 1
    surprise += min(len(BREAKTHROUGH_STRONG.findall(blob)), 1)
    return min(impact, 7), min(surprise, 7)


def main():
    ap = argparse.ArgumentParser(description="Breakthrough watcher (auto-publish)")
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true",
                    help="collect+gate only; no writes, no commit, no push")
    args = ap.parse_args()

    rows = load_json(DBJSON, [])
    seen = set(load_json(SEEN, []))
    known_urls = {r.get("url") for r in rows}
    known_titles_norm = {
        re.sub(r"[^a-z0-9]", "", r["title"].lower()) for r in rows
    }

    today = datetime.date.today().isoformat()
    promoted, skipped = [], 0
    for item in collect():
        if len(promoted) >= args.limit:
            break
        url = item["url"]
        title = item["title"].strip()
        if not url or not title or len(title) < 15:
            continue
        blob = f"{title} {item['snippet']}"
        if not BREAKTHROUGH_STRONG.search(blob) or not SOFT_CONTEXT.search(blob):
            continue
        if url in seen or url in known_urls:
            continue
        tnorm = re.sub(r"[^a-z0-9]", "", title.lower())
        if tnorm in known_titles_norm:
            continue
        year_s = (item["date"] or today)[:4]
        if not year_s.isdigit() or int(year_s) < 2024:
            continue  # the timeline covers history; the watcher adds current events
        year = int(year_s)
        imp, sur = score(item)
        org = guess_org(blob)
        eid = f"{year}-{slugify(title)}"
        base = eid
        n = 1
        while eid in {r["id"] for r in rows}:
            n += 1
            eid = f"{base}-auto{n}"
        rows.append({
            "id": eid,
            "year": year,
            "date": (item["date"] or today)[:10],
            "era": "live-discovery",
            "title": title,
            "org": org,
            "people": [],
            "paper": f"auto-flagged via {item['source']}",
            "url": url,
            "what_it_broke": f"{item['snippet'].strip()} [Source: {item['source']}].",
            "why_impossible": "Not yet assessed - auto-ingested by the watcher feed pending a curation pass.",
            "category": guess_category(blob),
            "impact": imp,
            "surprise": sur,
            "tags": ["auto", item["source"].split()[0].lower()],
        })
        promoted.append((eid, title, url))

    if args.dry_run:
        print(f"[probe:dry-run] would promote {len(promoted)} item(s)")
        for eid, t, u in promoted:
            print(f"  - {t}\n    {u}")
        return

    if not promoted:
        print(f"[probe] 0 new entr(ies) (total in db: {len(rows)})")
        return

    save_json(DBJSON, rows)
    seen.update(u for _, _, u in promoted)
    save_json(SEEN, sorted(seen))
    subprocess.run([sys.executable, os.path.join(ROOT, "build_csv.py")], check=True)

    def git(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)

    lines = "\n".join(f"- {t}" for _, t, _ in promoted)
    git("add", "data/breakthroughs.json", "data/breakthroughs.csv", "seen_urls.json")
    c = git("commit", "-m", f"[auto-watch] +{len(promoted)} entries\n\n{lines}")
    if c.returncode != 0:
        print(f"[err] commit failed: {c.stderr.strip()}")
        sys.exit(1)
    p = git("pull", "--rebase", "-q", "origin", "main")
    if p.returncode != 0:
        print(f"[warn] rebase issue: {p.stderr.strip()}")
    pu = git("push", "-q", "origin", "main")
    if pu.returncode != 0:
        print(f"[err] push failed: {pu.stderr.strip()}")
        sys.exit(1)
    print(f"[probe] +{len(promoted)} entr(ies) -> db={len(rows)}, committed & pushed")
    for eid, t, _u in promoted:
        print(f"  + {eid}: {t[:80]}")


if __name__ == "__main__":
    main()
