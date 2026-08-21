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

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
