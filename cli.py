#!/usr/bin/env python3
"""
cli.py - Query the AI Breakthroughs dataset from the terminal.

Usage:
  python3 cli.py list [--era ERA] [--category CAT] [--min-impact N]
  python3 cli.py top [--by impact|surprise|combined] [-n N]
  python3 cli.py get <id>
  python3 cli.py search <term>
  python3 cli.py stats

Zero-dependency stdlib only.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402


def _row(r):
    people = ", ".join(json.loads(r["people"])) if r.get("people") else ""
    return (f"[{r['year']}] {r['title']}\n"
            f"    who: {r['org']} ({people})\n"
            f"    paper: {r['paper']} | {r['url']}\n"
            f"    impact {r['impact']}/10 · surprise {r['surprise']}/10 · "
            f"combined {(r['impact'] + r['surprise']) / 2:.1f}")


def cmd_list(args):
    sql = "SELECT * FROM breakthroughs WHERE 1=1"
    params = []
    if args.era:
        sql += " AND era = ?"
        params.append(args.era)
    if args.category:
        sql += " AND category = ?"
        params.append(args.category)
    if args.min_impact:
        sql += " AND impact >= ?"
        params.append(args.min_impact)
    sql += " ORDER BY year ASC"
    rows = db.query(sql, tuple(params))
    for r in rows:
        print(_row(r))
        print()
    print(f"{len(rows)} breakthroughs")


def cmd_top(args):
    order = {"impact": "impact DESC", "surprise": "surprise DESC",
             "combined": "(impact + surprise) DESC"}[args.by]
    rows = db.query(f"SELECT * FROM breakthroughs ORDER BY {order}, year ASC LIMIT ?", (args.n,))
    for i, r in enumerate(rows, 1):
        print(f"#{i} {_row(r)}")
        print()


def cmd_get(args):
    rows = db.query("SELECT * FROM breakthroughs WHERE id = ?", (args.id,))
    if not rows:
        sys.exit(f"No breakthrough with id '{args.id}'")
    r = rows[0]
    print(_row(r))
    print("\n  What it broke:", r["what_it_broke"])
    print("\n  Why it seemed impossible:", r["why_impossible"])


def cmd_search(args):
    like = f"%{args.term}%"
    rows = db.query(
        """SELECT * FROM breakthroughs WHERE
           title LIKE ? OR org LIKE ? OR paper LIKE ? OR what_it_broke LIKE ?
           OR why_impossible LIKE ? OR tags LIKE ? OR people LIKE ?
           ORDER BY year ASC""", (like,) * 7)
    for r in rows:
        print(_row(r))
        print()
    print(f"{len(rows)} matches")


def cmd_stats(args):
    total = db.query("SELECT COUNT(*) c FROM breakthroughs")[0]["c"]
    eras = db.query(
        "SELECT era, COUNT(*) n, MIN(year) lo, MAX(year) hi "
        "FROM breakthroughs GROUP BY era ORDER BY MIN(year)")
    cats = db.query(
        "SELECT category, COUNT(*) n FROM breakthroughs GROUP BY category ORDER BY n DESC")
    top = db.query(
        "SELECT year, title FROM breakthroughs ORDER BY (impact+surprise) DESC LIMIT 5")
    print(f"Total breakthroughs: {total}")
    print("\nBy era:")
    for e in eras:
        print(f"  {e['era']:<22} {e['n']:>2}  ({e['lo']}–{e['hi']})")
    print("\nBy category:")
    for c in cats:
        print(f"  {c['category']:<26} {c['n']:>2}")
    print("\nTop 5 by combined score:")
    for t in top:
        print(f"  [{t['year']}] {t['title']}")


NOTE_ADJ = ["Melodic", "Wistful", "Curious", "Ambiguous", "Sardonic",
            "Neighborly", "Vermilion", "Umber", "Quartz", "Rhubarb"]
NOTE_ANIMAL = ["Capybara", "Wombat", "Tapir", "Chickadee", "Hedgehog",
               "Pangolin", "Ibex", "Okapi"]


def _note_status(n):
    """ABCoreModel v0.1 - mirrors noteStatus() in index.html. Deterministic:
    helpful requires the note itself to cite >=2 distinct source domains."""
    import urllib.parse
    if n.get("classification") == "not_helpful":
        return "CURRENTLY_RATED_NOT_HELPFUL"
    domains = set()
    for s in n.get("sources", []):
        netloc = urllib.parse.urlsplit(s).netloc.lower().replace("www.", "")
        if netloc:
            domains.add(netloc)
    if (n.get("classification") == "helpful"
            and len(domains) >= 2 and len(n.get("text", "")) >= 120):
        return "CURRENTLY_RATED_HELPFUL"
    return "NEEDS_MORE_RATINGS"


def cmd_note(args):
    import random
    with open(db.JSON_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    entry = next((r for r in rows if r["id"] == args.id), None)
    if not entry:
        sys.exit(f"No breakthrough with id '{args.id}'")
    note = {
        "id": str(random.getrandbits(62)),
        "created": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "author": {
            "alias": args.alias or (f"{random.choice(NOTE_ADJ)} "
                                    f"{random.choice(NOTE_ADJ)} "
                                    f"{random.choice(NOTE_ANIMAL)}"),
            "type": args.author_type,
            "impact": args.impact or random.randint(500, 4000),
        },
        "classification": args.classification,
        "tags": [t.strip() for t in args.tags.split(",") if t.strip()],
        "text": args.text,
        "sources": [s.strip() for s in args.sources.split(",") if s.strip()],
    }
    entry.setdefault("notes", []).append(note)
    with open(db.JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"note {note['id']} added to {args.id}")
    print(f"  status: {_note_status(note)}")
    print(f"  author: {note['author']['alias']} "
          f"({note['author']['impact']} Writing Impact)")


def main():
    p = argparse.ArgumentParser(description="AI Breakthroughs CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list")
    sp.add_argument("--era")
    sp.add_argument("--category")
    sp.add_argument("--min-impact", type=int)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("top")
    sp.add_argument("--by", choices=["impact", "surprise", "combined"], default="combined")
    sp.add_argument("-n", type=int, default=10)
    sp.set_defaults(func=cmd_top)

    sp = sub.add_parser("get")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("search")
    sp.add_argument("term")
    sp.set_defaults(func=cmd_search)

    sub.add_parser("stats").set_defaults(func=cmd_stats)

    sp = sub.add_parser("note", help="attach a Community-Notes-style note")
    sp.add_argument("id")
    sp.add_argument("--text", required=True)
    sp.add_argument("--sources", default="",
                    help="comma-separated URLs (>=2 distinct domains for HELPFUL)")
    sp.add_argument("--tags", default="helpfulImportantContext",
                    help="comma-separated CN tag keys")
    sp.add_argument("--classification", choices=["helpful", "not_helpful"],
                    default="helpful")
    sp.add_argument("--alias", help="note-writer alias (X-style, auto-generated if omitted)")
    sp.add_argument("--author-type", choices=["ai", "human"], default="ai")
    sp.add_argument("--impact", type=int, help="Writing Impact number")
    sp.set_defaults(func=cmd_note)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
