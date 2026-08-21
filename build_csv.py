#!/usr/bin/env python3
"""
build_csv.py - Export the canonical dataset to CSV for spreadsheets/pandas.

Zero-dependency stdlib only.
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(HERE, "data", "breakthroughs.json")
CSV_PATH = os.path.join(HERE, "data", "breakthroughs.csv")

FIELDS = ["id", "year", "date", "era", "title", "org", "people", "paper",
          "url", "what_it_broke", "why_impossible", "category", "impact",
          "surprise", "tags"]


def main():
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        rows = json.load(f)
    rows.sort(key=lambda r: r["year"])
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            flat = dict(r)
            flat["people"] = "; ".join(r.get("people", []))
            flat["tags"] = "; ".join(r.get("tags", []))
            w.writerow({k: flat.get(k, "") for k in FIELDS})
    print(f"Wrote {len(rows)} rows -> {CSV_PATH}")


if __name__ == "__main__":
    main()
