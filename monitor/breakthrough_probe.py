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
import email.utils
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
DECISIONS = os.path.join(DATA, "review_decisions.json")
REMOVED = os.path.join(DATA, "removed_entries.json")
HOTLIST = os.path.join(ROOT, "HOTLIST.md")

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

# Marketing/award/PR fluff that pattern-matches "breakthrough" language but
# is not a scientific or capability milestone.
BREAKTHROUGH_NEGATIVE = re.compile(
    r"(awards?|of the year|recogniz\w+|honors?|gala|names? \w+ .{0,30}platform|"
    r"celebrates|announces winners|nominat|webinar|sponsored|press release digest)",
    re.I,
)

RECENCY_DAYS = 14

# arXiv abstracts routinely claim "outperforms SOTA baselines" - that is
# boilerplate, not a milestone. Research listings must clear a higher bar.
ARXIV_MIN = re.compile(
    r"(breakthrough|first[- ]ever|for the first time|new record|world record|"
    r"superhuman|millennium prize|nobel|gold.medal|"
    r"beats? .{0,30}(?:gpt|gemini|claude|frontier|human experts?|physicians?)|"
    r"surpass\w* .{0,30}(?:human|expert|physician))",
    re.I,
)

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

REDDITS = ["singularity", "LocalLLaMA", "artificial", "technology",
           "MachineLearning", "OpenAI", "Anthropic", "ClaudeAI",
           "generativeai", "agi", "machinelearningnews", "largelanguagemodels",
           "robotics", "healthcare", "futurology"]
REDDIT_QUERY = "AI breakthrough OR state-of-the-art OR open weights OR first ever OR beats OR record"

GN_QUERIES = [
    "AI breakthrough announcement",
    "new AI model outperforms state of the art",
    "AI sets record benchmark",
    "lab releases open weights model",
    "AI protein OR drug discovery milestone",
    "humanoid robot achieves first",
    "AI weather OR fusion OR materials discovery breakthrough",
    "AI math proof millennium problem",
    "robotaxi expansion fully driverless milestone",
    "AI chip announcement gigawatt fab",
    "AI diagnostic FDA approval",
    "AI scientific discovery first",
]

X_ACCOUNTS = [
    # AI news wires / aggregators
    "_akhaliq", "TheRundownAI", "AIHighlight", "swyx", "smolix",
    # labs
    "AnthropicAI", "OpenAI", "GoogleDeepMind", "AIatMeta", "xai",
    "huggingface", "DeepSeek", "Qwen", "MoonshotAI", "ZhihuZAI",
    # robotics
    "Figure_robot", "unitreerobotics", "Waymo", "Tesla_Optimus",
    # research commentary
    "karpathy", "demishassabis", "ilyasut", "sama", "jackclarkSF",
    "schmidhuber", "DrJimFan",
]
# Public RSSHub twitter routes have been auth-gated/dead since 2024; keep the
# collector for when an authed instance is available. Override via env:
#   RSSHUB_INSTANCES="https://my-rsshub.example" python3 monitor/breakthrough_probe.py
RSSHUB_INSTANCES = [
    u for u in os.environ.get("RSSHUB_INSTANCES", "").split(",") if u.strip()
] or ["https://rsshub.app", "https://rsshub.rssforever.com",
      "https://rsshub.pseudoyu.com"]

FEEDS = [
    # --- primary / official (labs publish here first) ---
    ("OpenAI News", "https://openai.com/news/rss.xml"),
    # anthropic.com publishes no RSS of its own; RSSHub mirrors scrape /news.
    # Two mirrors - dedupe handles the overlap if both are up.
    ("Anthropic News", "https://rsshub.rssforever.com/anthropic/news"),
    ("Anthropic News (mirror)", "https://rsshub.ktachibana.party/anthropic/news"),
    ("DeepMind Blog", "https://deepmind.google/blog/rss.xml"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    # releases.rdf was retired; releases.xml is the iPressroom feed.
    ("NVIDIA Newsroom", "https://nvidianews.nvidia.com/releases.xml"),
    ("NVIDIA Blog", "https://blogs.nvidia.com/feed/"),
    ("Nature", "https://www.nature.com/nature.rss"),
    ("Science", "https://www.science.org/rss/news_current.xml"),
    ("NEJM", "https://www.nejm.org/action/showFeed?jc=nejm&type=etoc"),
    # --- press / aggregators ---
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
    (r"metr|redwood", "METR / Redwood"),
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


def parse_date(s):
    """Normalize ISO-8601 or RFC822 dates to ISO yyyy-mm-dd ('' if unknown)."""
    s = (s or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    try:
        return email.utils.parsedate_to_datetime(s).date().isoformat()
    except Exception:
        return ""


def within_window(datestr):
    d = parse_date(datestr)
    if not d:
        return True  # unknown date: keep, the year gate re-checks later
    cutoff = (datetime.date.today() - datetime.timedelta(days=RECENCY_DAYS)).isoformat()
    return d >= cutoff


def reddit_search(limit):
    out = []
    for sub in REDDITS:
        for host in ("www.reddit.com", "old.reddit.com"):
            url = (f"https://{host}/r/{sub}/search.json"
                   f"?q={urllib.parse.quote(REDDIT_QUERY)}&sort=new&limit={limit}")
            try:
                d = json.loads(fetch(url))
                if not isinstance(d, dict) or "data" not in d:
                    continue
                for c in d.get("data", {}).get("children", []):
                    p = c.get("data", {})
                    d8 = datetime.datetime.fromtimestamp(
                        p.get("created_utc", 0)).strftime("%Y-%m-%d")
                    if not within_window(d8):
                        continue
                    out.append({
                        "title": p.get("title", ""),
                        "url": f"https://www.reddit.com{p.get('permalink', '')}",
                        "source": f"Reddit r/{sub}",
                        "date": d8,
                        "snippet": (p.get("selftext") or "")[:300],
                    })
                break
            except Exception:
                continue
    return out


def twitter_rss(limit):
    """Best-effort X watchlist via RSSHub mirrors (skip when down).

    Twitter routes on public mirrors have been dead since 2024; this stays
    key-free and no-ops quietly unless RSSHUB_INSTANCES supplies a live
    (e.g. authed, self-hosted) instance.
    """
    out = []
    failed = []
    for inst in RSSHUB_INSTANCES:
        if out:
            break
        for handle in X_ACCOUNTS:
            try:
                raw = fetch(f"{inst}/twitter/user/{handle}", timeout=5)
                root = ET.fromstring(raw)
                entries = root.findall("{http://www.w3.org/2005/Atom}entry")
                for e in entries[:limit]:
                    t = e.findtext("{http://www.w3.org/2005/Atom}title", "") or ""
                    lel = e.find("{http://www.w3.org/2005/Atom}link")
                    link = lel.get("href") if lel is not None else ""
                    d = (e.findtext("{http://www.w3.org/2005/Atom}updated", ""))[:10]
                    if not within_window(d):
                        continue
                    out.append({"title": t, "url": link, "source": f"X @{handle}",
                                "date": d, "snippet": t[:300]})
                if out:
                    break
            except Exception:
                failed.append(inst)
                break
    if not out and failed:
        print(f"[warn] X watchlist unavailable on {len(failed)}/{len(RSSHUB_INSTANCES)} "
              f"mirror(s) - set RSSHUB_INSTANCES to add a live instance")
    return out


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
                            "date": parse_date(e.findtext("pubDate", "")),
                            "snippet": desc[:400]})
        except Exception as ex:
            print(f"[warn] {name} failed: {ex}")

    out += reddit_search(8)
    out += twitter_rss(8)

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
                d = parse_date(e.findtext("pubDate", ""))
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
                    d = parse_date(e.findtext("pubDate", ""))
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


def score_entry(item):
    """Conservative heuristic scoring - auto entries never reach gold tier."""
    blob = f"{item['title']} {item['snippet']}"
    impact, surprise = 6, 5
    if re.search(r"first[- ]ever|for the first time|superhuman", blob, re.I):
        impact += 1
    if re.search(r"state[- ]of[- ]the[- ]art|\bsota\b|outperforms?|surpass", blob, re.I):
        impact += 1
    surprise += min(len(BREAKTHROUGH_STRONG.findall(blob)), 1)
    return min(impact, 7), min(surprise, 7)


def hot_score(item):
    """Mirror of AIincidents' hot-signal idea: 3+ means ping the human."""
    blob = f"{item['title']} {item['snippet']}"
    s = len(BREAKTHROUGH_STRONG.findall(blob))
    try:
        host = urllib.parse.urlsplit(item["url"]).netloc.lower()
    except Exception:
        host = ""
    if re.search(r"(openai\.com|anthropic\.com|deepmind\.google|nature\.com|"
                 r"science\.org|nejm\.org|nvidia\.com|huggingface\.co)", host):
        s += 2  # primary-source domain: big weight
    if re.search(r"superhuman|first[- ]ever|new record|millennium|nobel", blob, re.I):
        s += 1
    return s


def notify_hot(hot_items):
    """Append to HOTLIST.md and ping macOS Notification Center."""
    if not hot_items:
        return
    stamp = datetime.datetime.now().strftime("%F %T")
    with open(HOTLIST, "a", encoding="utf-8") as f:
        f.write(f"## {stamp}\n")
        for it in hot_items:
            f.write(f"- **{it['title']}** ({it['source']}, {it['date']})\n  {it['url']}\n")
        f.write("\n")
    try:
        msg = hot_items[0]["title"][:110].replace('"', "'")
        subprocess.run(["osascript", "-e",
                        f'display notification "{msg}" with title '
                        f'"AIBreakthroughs: {len(hot_items)} hot signal(s)"'],
                       timeout=10, check=False)
    except Exception:
        pass


def apply_decisions(rows):
    """Apply data/review_decisions.json (exported from review.html).

    gold  -> impact/surprise bumped to 9/9, 'auto' tag replaced by 'reviewed'
    keep  -> 'auto' tag replaced by 'reviewed' (scores stay)
    remove-> entry moved to data/removed_entries.json
    Processed decisions are consumed (file rewritten without them).
    Returns (rows, applied_count).
    """
    if not os.path.exists(DECISIONS):
        return rows, 0
    dec = load_json(DECISIONS, {}).get("decisions", {})
    if not dec:
        return rows, 0
    removed = load_json(REMOVED, [])
    applied = 0
    by_id = {r["id"]: r for r in rows}
    for eid, d in list(dec.items()):
        action = d.get("decision")
        if action not in ("gold", "keep", "remove") or eid not in by_id:
            dec.pop(eid, None)
            continue
        r = by_id.pop(eid)
        if action == "remove":
            r["removed_reason"] = "human-review"
            r["removed_at"] = datetime.date.today().isoformat()
            removed.append(r)
        else:
            tags = [t for t in r.get("tags", []) if t != "auto"]
            tags.append("reviewed")
            r["tags"] = tags
            if action == "gold":
                r["impact"], r["surprise"] = 9, 9
            by_id[eid] = r
        applied += 1
        dec.pop(eid, None)
    save_json(DBJSON, list(by_id.values()))
    if removed:
        save_json(REMOVED, removed)
    if applied:
        save_json(DECISIONS, {"exported": load_json(DECISIONS, {}).get("exported"),
                              "decisions": dec})
    return list(by_id.values()), applied


def main():
    ap = argparse.ArgumentParser(description="Breakthrough watcher (auto-publish)")
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true",
                    help="collect+gate only; no writes, no commit, no push")
    args = ap.parse_args()

    rows = load_json(DBJSON, [])
    rows, applied = apply_decisions(rows)
    if applied:
        print(f"[decisions] applied {applied} human review decision(s)")
    seen = set(load_json(SEEN, []))
    known_urls = {r.get("url") for r in rows}
    known_titles_norm = {
        re.sub(r"[^a-z0-9]", "", r["title"].lower()) for r in rows
    }

    today = datetime.date.today().isoformat()
    promoted, skipped = [], 0
    raw_items = collect()
    staged = []
    for item in raw_items:
        if len(promoted) >= args.limit:
            break
        url = item["url"]
        title = item["title"].strip()
        if not url or not title or len(title) < 15:
            continue
        blob = f"{title} {item['snippet']}"
        if not BREAKTHROUGH_STRONG.search(blob) or not SOFT_CONTEXT.search(blob):
            continue
        if BREAKTHROUGH_NEGATIVE.search(blob):
            continue
        if item["source"].startswith("arXiv") and not ARXIV_MIN.search(blob):
            continue
        if url in seen or url in known_urls:
            continue
        tnorm = re.sub(r"[^a-z0-9]", "", title.lower())
        if tnorm in known_titles_norm:
            continue
        year_s = (parse_date(item["date"]) or "")[:4]
        if not year_s.isdigit() or int(year_s) < 2024:
            continue  # the timeline covers history; the watcher adds current events
        year = int(year_s)
        item_date = parse_date(item["date"]) or today
        imp, sur = score_entry(item)
        org = guess_org(blob)
        eid = f"{year}-{slugify(title)}"
        base = eid
        n = 1
        while eid in {r["id"] for r in rows}:
            n += 1
            eid = f"{base}-auto{n}"
        staged.append({
            "id": eid,
            "title": title,
            "url": url,
            "source": item["source"],
            "date": item_date,
            "snippet": item["snippet"].strip()[:400],
            "impact": imp,
            "surprise": sur,
            "org_hint": org,
        })
        promoted.append((eid, title, url))

    if args.dry_run:
        print(f"[probe:dry-run] staged {len(staged)} candidate(s) for lane engine")
        for s in staged:
            print(f"  - [{s['impact']}/{s['surprise']}] {s['title'][:70]}")
        return

    # hand staged candidates to the three-lane promotion engine
    if staged:
        cand_path = os.path.join(ROOT, "data", "watch_candidates.json")
        pool = load_json(cand_path, [])
        known = {c.get("url") for c in pool}
        pool += [s for s in staged if s["url"] not in known]
        save_json(cand_path, pool)
        seen.update(s["url"] for s in staged)

    # run the lane engine (it saves candidates, promotes, builds, pushes)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "monitor", "auto_promote.py")],
                       capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    print(out or "[promote] (no output)")
    if applied:
        subprocess.run([sys.executable, os.path.join(ROOT, "build_csv.py")], check=True)
        def g0(*a):
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        g0("add", "data/breakthroughs.json", "data/breakthroughs.csv",
           "data/review_decisions.json", "data/removed_entries.json")
        g0("commit", "-m", f"[review] {applied} decision(s) applied [auto]")
        g0("pull", "--rebase", "-q", "origin", "main")
        g0("push", "-q", "origin", "main")
    if not staged:
        print(f"[probe] 0 new candidates (db: {len(rows)})")
    return

if __name__ == "__main__":
    main()
