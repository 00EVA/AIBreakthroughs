#!/usr/bin/env python3
"""
api.py - Zero-dependency JSON API for the AI Breakthroughs timeline.

Endpoints:
  GET /                     -> service info
  GET /breakthroughs        -> all entries (supports ?era=&category=&min_impact=)
  GET /breakthroughs/<id>   -> single entry by id
  GET /top                  -> ranked by combined score (?by=impact|surprise|combined&n=N)
  GET /stats                -> aggregate counts by era/category
  GET /search?q=<term>      -> full-text-ish search

Run:  python3 api.py [port]     (default 8787)
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, indent=1).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        parts = [p for p in parsed.path.split("/") if p]

        try:
            if not parts:
                return self._send(200, {
                    "service": "ai-breakthroughs-api",
                    "endpoints": ["/breakthroughs", "/breakthroughs/<id>",
                                  "/top", "/stats", "/search?q="],
                })

            if parts[0] == "breakthroughs":
                if len(parts) == 2:
                    rows = db.query("SELECT * FROM breakthroughs WHERE id = ?", (parts[1],))
                    return self._send(200, rows[0] if rows else {"error": "not found"})
                sql = "SELECT * FROM breakthroughs WHERE 1=1"
                params = []
                if "era" in qs:
                    sql += " AND era = ?"; params.append(qs["era"][0])
                if "category" in qs:
                    sql += " AND category = ?"; params.append(qs["category"][0])
                if "min_impact" in qs:
                    sql += " AND impact >= ?"; params.append(int(qs["min_impact"][0]))
                sql += " ORDER BY year ASC"
                return self._send(200, db.query(sql, tuple(params)))

            if parts[0] == "top":
                by = qs.get("by", ["combined"])[0]
                order = {"impact": "impact DESC", "surprise": "surprise DESC",
                         "combined": "(impact+surprise) DESC"}.get(by)
                if not order:
                    return self._send(400, {"error": "bad 'by'"})
                n = min(int(qs.get("n", ["10"])[0]), 100)
                return self._send(200, db.query(
                    f"SELECT * FROM breakthroughs ORDER BY {order}, year LIMIT ?", (n,)))

            if parts[0] == "stats":
                eras = db.query(
                    "SELECT era, COUNT(*) n, MIN(year) lo, MAX(year) hi "
                    "FROM breakthroughs GROUP BY era ORDER BY MIN(year)")
                cats = db.query(
                    "SELECT category, COUNT(*) n FROM breakthroughs "
                    "GROUP BY category ORDER BY n DESC")
                return self._send(200, {"total": len(db.all_breakthroughs()),
                                        "by_era": eras, "by_category": cats})

            if parts[0] == "search":
                q = qs.get("q", [""])
                like = f"%{q[0]}%"
                return self._send(200, db.query(
                    """SELECT * FROM breakthroughs WHERE title LIKE ? OR org LIKE ?
                       OR paper LIKE ? OR what_it_broke LIKE ? OR why_impossible LIKE ?
                       OR tags LIKE ? OR people LIKE ? ORDER BY year""",
                    (like,) * 7))

            return self._send(404, {"error": "unknown endpoint"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})

    def log_message(self, fmt, *args):  # quieter logs
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print(f"Serving AI Breakthroughs API on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
