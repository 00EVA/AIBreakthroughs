#!/usr/bin/env python3
"""
auto_promote.py - Three-lane promotion engine for AIBreakthroughs.

Mirrors the ai-escape-log lane model, mapped to breakthrough discovery:

  LANE 1 (green)  AUTO-PROMOTE to 'high' tier immediately, no human:
                    - Primary-source milestone language ("met its primary
                      endpoint", "first positive Phase 3", "peer-reviewed
                      in Nature/Science/NEJM") on an official domain
                    - Widespread: 3+ independent outlets on same story
                    - Journal-published (nature.com/science.org/nejm.org
                      byline) + heat 4+
                  Every promotion stamps its reason for later audit.

  LANE 2 (yellow) GRAY ZONE - ingest now, modest: trusted official source
                  or 2-outlet spread, but unproven -> enters at capped
                  scores, importance 'minor', note "pending human rating".
                  Visible behind the tier filter on review.html.

  LANE 3 (orange) HUMAN REVIEW QUEUE: strong signal, untrusted source
                  (press commentary, aggregators, research blogs).
                  Posted to HOTLIST.md + macOS notification (hot_score>=3).
                  review.html decisions (data/review_decisions.json)
                  always override machine ratings.

Safety rails: nothing is ever deleted (removals archive with reason),
human decisions win over machine ratings, URL/title dedupe everywhere.

Run order: watcher appends candidates -> auto_promote escalates -> any
promotion rebuilds CSV, commits, pushes (Pages refreshes).

Usage: python3 monitor/auto_promote.py [--dry-run]
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
CAND = os.path.join(DATA, "watch_candidates.json")
DBJSON = os.path.join(DATA, "breakthroughs.json")
ARCHIVE = os.path.join(DATA, "promote_archive.json")
DECS = os.path.join(DATA, "review_decisions.json")
SEEN = os.path.join(ROOT, "seen_urls.json")

OFFICIAL = re.compile(
    r"(openai\.com|anthropic\.com|deepmind\.google|deepmind\.com|blog\.google|"
    r"ai\.googleblog|huggingface\.co|nvidia\.com|microsoft\.com|modername|"
    r"merck\.com|modernatx\.com|isomorphic\.com|waymo\.com|cerebras\.ai|"
    r"moonshotai|qwen|z\.ai|mistral\.ai|x\.ai)",
    re.I,
)
JOURNAL = re.compile(r"(nature\.com|science\.org|nejm\.org|cell\.com|science\.org)", re.I)

# Milestone language that separates REAL results from routine progress.
MILESTONE = re.compile(
    r"(met (?:its |the )?(?:primary|key|main) endpoint|met both (?:its |the )?(?:primary|key|main)? ?endpoints|"
    r"positive phase (?:1|2|3)|phase 3 (?:success|readout|win)|first positive phase|"
    r"peer[- ]reviewed (?:in|at)|published in nature|published in science|"
    r"first[- ]ever|for the first time|world record|new record|"
    r"superhuman|passes? .{0,25}(?:turing|bar exam|USMLE)|"
    r"beats? .{0,30}(?:gpt|gemini|claude|frontier|human (?:experts?|champions?|physicians?))|"
    r"outperform\w* .{0,30}(?:human|physician|expert|frontier)|"
    r"open[- ]?weights? (?:release|published|shipped)|approved (?:by the )?FDA|"
    r"regulatory approval|formally verified|lean[- ]verified)",
    re.I,
)

# Breakthrough-adjacent domains for corroboration counting
OUTLET = re.compile(r"\.(com|org|net|edu|gov|io|ai|co)(/|$)", re.I)


def load(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d


def save(p, x):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(x, f, indent=2, ensure_ascii=False)


def norm_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())[:80]


def domain(u):
    try:
        return re.sub(r"^www\.", "", __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(u).netloc.lower())
    except Exception:
        return ""


def heat(item):
    """Keyword heat: milestone language density + primary-source bonus."""
    blob = f"{item.get('title','')} {item.get('snippet','')}"
    s = len(MILESTONE.findall(blob))
    if JOURNAL.search(item.get("url", "")):
        s += 2
    if OFFICIAL.search(item.get("url", "")):
        s += 1
    return s


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cands = load(CAND, [])
    rows = load(DBJSON, [])
    archive = load(ARCHIVE, [])
    decisions = load(DECS, {})
    dec_map = decisions.get("decisions", decisions) if isinstance(decisions, dict) else {}
    seen = set(load(SEEN, []))

    known_urls = {r.get("url") for r in rows} | {a.get("url") for a in archive}
    known_titles = {norm_title(r.get("title")) for r in rows}

    # corroboration index across the candidate pool (distinct domains/story)
    outlets = {}
    for c in cands:
        tn = norm_title(c.get("title"))
        if tn:
            outlets.setdefault(tn, set()).add(domain(c.get("url", "")) or c.get("source", ""))
    def spread(c):
        return len({s for s in outlets.get(norm_title(c.get("title")), set()) if s})

    today = datetime.date.today().isoformat()
    promoted, kept, pruned = [], [], []

    # ---------- human decisions first (always override) ----------
    for eid, d in list(dec_map.items()):
        action = d.get("decision")
        r = next((x for x in rows if x["id"] == eid), None)
        if not r or action not in ("gold", "keep", "remove"):
            continue
        if action == "remove":
            rows.remove(r)
            r["removed_reason"] = "human-review"
            r["removed_at"] = today
            archive.append({**r, "reason": "human-remove"})
        else:
            tags = [t for t in r.get("tags", []) if t != "auto"] + ["reviewed"]
            r["tags"] = tags
            if action == "gold":
                r["impact"], r["surprise"] = 9, 9
                r["tier"] = "huge"
            r["tier"] = r.get("tier", "high")
        dec_map.pop(eid, None)
        save(DECS, {"exported": decisions.get("exported"), "decisions": dec_map})
        promoted.append({"id": eid, "note": f"human-rated: {action}"})

    for c in cands:
        url = c.get("url") or ""
        title = (c.get("title") or "").strip()
        blob = f"{title} {c.get('snippet') or ''} {url}"
        tn = norm_title(title)
        h = heat(c)
        sp = spread(c)
        is_official = bool(OFFICIAL.search(url))
        is_journal = bool(JOURNAL.search(url))

        def archive_it(reason):
            archive.append({**c, "reason": reason, "archived_at": today})

        if url in known_urls or tn in known_titles:
            archive_it("duplicate")
            continue

        # ---- LANE 1: auto-promote to 'high', immediate ----
        milestone = MILESTONE.search(blob)
        if (is_official and milestone and (h >= 3 or sp >= 2)) or \
           (is_journal and milestone and h >= 3) or \
           (sp >= 3 and milestone):
            reason = ("journal-published milestone" if is_journal else
                      "official source + milestone language" if is_official else
                      f"{sp} independent outlets covering")
            entry = make_entry(c, "high", f"auto-promoted: {reason}")
            rows.append(entry)
            known_urls.add(url)
            known_titles.add(tn)
            promoted.append(entry)
            continue

        # ---- LANE 2: gray zone - ingest modest, pending rating ----
        if (is_official and h >= 2) or (sp >= 2 and h >= 2):
            entry = make_entry(c, "minor", "auto-ingested - pending human rating")
            rows.append(entry)
            known_urls.add(url)
            known_titles.add(tn)
            promoted.append(entry)
            continue

        # ---- LANE 3: hold for human (stays in candidates) ----
        kept.append(c)

    if args.dry_run:
        print(f"[promote:dry] lane1/2 promote {len(promoted)}, lane3 hold {len(kept)}, "
              f"archive {len(archive)}")
        for e in promoted[:8]:
            print(f"  [{e['tier']}] {e['title'][:70]}")
        return

    save(CAND, kept)          # lane 3 stays queued for review.html/hotlist
    save(ARCHIVE, archive)
    if promoted:
        save(DBJSON, rows)
        save(SEEN, sorted(seen | {e["url"] for e in promoted}))
        subprocess.run([sys.executable, os.path.join(ROOT, "build_csv.py")], check=True)
        git("add", "data/breakthroughs.json", "data/breakthroughs.csv",
            "data/watch_candidates.json", "data/promote_archive.json",
            "data/review_decisions.json", "seen_urls.json")
        msg = ("[auto-promote] +%d entr(ies)\n\n" % len(promoted)) + \
              "\n".join(f"- [{e['tier']}] {e['title'][:70]}" for e in promoted[:15])
        c = git("commit", "-m", msg)
        if c.returncode == 0:
            git("pull", "--rebase", "-q", "origin", "main")
            p = git("push", "-q", "origin", "main")
            pushed = "pushed (Pages refreshes)" if p.returncode == 0 else "PUSH FAILED"
        else:
            pushed = "nothing to commit"
        print(f"[promote] +{len(promoted)} entr(ies) -> db={len(rows)} | {pushed}")
        for e in promoted[:10]:
            print(f"  + [{e['tier']}] {e['title'][:70]}")
    else:
        print(f"[promote] 0 promoted | lane3 holding {len(kept)} for review | "
              f"archive {len(archive)}")


def make_entry(c, tier, note):
    imp = 7 if tier == "high" else 5
    sur = 6 if tier == "high" else 5
    y = (c.get("date") or today_iso())[:4]
    slug = re.sub(r"[^a-z0-9]+", "-", c.get("title", "").lower())[:48].strip("-")
    blob = f"{c.get('title','')} {c.get('snippet','')}"
    cat = "ai-for-science" if re.search(r"(drug|protein|diagnos|medicine|cancer|trial|weather|material|fusion|crystal|quantum|math)", blob, re.I) else \
          "hardware-milestone" if re.search(r"(chip|gpu|wafer|lpu|fab|silicon|inference silicon)", blob, re.I) else \
          "open-ecosystem" if re.search(r"(open.?weight|apache|mit license)", blob, re.I) else \
          "robotics" if re.search(r"(robot|humanoid)", blob, re.I) else "capability-leap"
    return {
        "id": f"{y}-{slug}" or f"auto-{c[:20]}",
        "year": int(y) if y.isdigit() else datetime.date.today().year,
        "date": c.get("date") or today_iso(),
        "era": "live-discovery",
        "title": c.get("title", "")[:200],
        "org": "Unattributed",
        "people": [],
        "paper": f"auto-flagged via {c.get('source','?')}",
        "url": c.get("url", ""),
        "source_hint": c.get("source", ""),
        "what_it_broke": (c.get("snippet") or c.get("title", "")).strip()[:400] + f" [Source: {c.get('source','?')}]",
        "why_impossible": f"Pending assessment - {note}.",
        "category": cat,
        "impact": imp,
        "surprise": sur,
        "tier": "high" if tier == "high" else "minor",
        "tags": ["auto", (c.get("source", "?").split()[0] or "?").lower()],
        "promote_note": note,
    }


def today_iso():
    return datetime.date.today().isoformat()


if __name__ == "__main__":
    main()
