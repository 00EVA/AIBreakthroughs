#!/bin/bash
# auto_refresh.sh - daily consistency pass for AIBreakthroughs.
# Ensures derived artifacts (csv, db) are in sync with the canonical JSON,
# applies any pending review decisions, and pushes if anything changed.
# Unlike the 2h watcher this runs even when no new entries were found.
set -u
cd /Users/tony/AIBreakthroughs || exit 1
LOG=/Users/tony/Library/Logs/aibreakthroughs_watch.log
{
  echo "[refresh] $(date '+%F %T') start"
  git pull --rebase -q origin main 2>/dev/null || echo "[refresh] pull skipped"
  python3 monitor/breakthrough_probe.py --limit 0 2>/dev/null || true
  python3 build_csv.py || exit 1
  git add data/ HOTLIST.md 2>/dev/null
  if git diff --cached --quiet; then
    echo "[refresh] no changes"
  else
    git commit -q -m "daily refresh: derived artifacts + review state [cron]"
    if git push -q origin main 2>/dev/null; then
      echo "[refresh] pushed (Pages will rebuild)"
    else
      echo "[refresh] push FAILED"
    fi
  fi
  echo "[refresh] done"
} >> "$LOG" 2>&1
