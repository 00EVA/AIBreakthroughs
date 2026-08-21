# AI Breakthroughs

> A public, machine-readable timeline of every major AI breakthrough — sourced
> to papers, ranked by **how far they pushed past what anyone thought was possible**.

From McCulloch & Pitts proving in 1943 that thought could be arithmetic on switches,
to AlphaGo's Move 37 (a move no human would play), to GPT-3's few-shot learning
appearing like a phase transition, to DeepSeek-R1 erasing ~$600B of assumed moat
in a single day — this is the record of heights we didn't think were climbable,
and the receipts for each one.

**Sister project:** [AI Escape Log](https://github.com/) (`~/AIincidents`) tracks models
breaking *out* of systems. This one tracks humans breaking *through* limits.

## The ranking: two axes

Each breakthrough carries two hand-curated scores (see methodology below):

| Score | Question it answers | Scale |
|---|---|---|
| `impact` | How much did this change what AI could do / what happened after? | 1–10 |
| `surprise` | How far past the expert consensus of its time did it land? | 1–10 |

**Combined score = mean(impact, surprise).** Combined ≥ 9.0 earns the
**★ impossible-tier** badge on the site — the moments where reality outran the
field's imagination:

- 2012 AlexNet — classical vision ended by two gaming GPUs
- 2016 AlphaGo + Move 37 — Go fell "a decade early," creatively
- 2020 GPT-3 emergence + Scaling Laws — capabilities appeared unbidden; progress became forecastable
- 2022 ChatGPT — fastest consumer adoption ever, from a tech no lab thought would detonate
- 2022 AlphaFold2 — a 50-year grand challenge closed and given away
- 2024 o1 — inference-time compute opened a second scaling axis
- 2025 DeepSeek-R1 — open weights + pure RL matched closed o1; markets repriced overnight

## Methodology & honest caveats

Scores are **editorial judgments**, not measurements — set at dataset-authoring time,
documented per-entry via `what_it_broke` and `why_impossible`. They are meant as a
starting point for argument, not an end to it. PRs that argue a re-score (with
reasoning) are welcome. Sources link to the original paper or primary technical
report wherever one exists; popular coverage is noted in summaries but never used
as the sole citation.

## Data

| File | Purpose |
|---|---|
| `data/breakthroughs.json` | Canonical, hand-curated dataset (source of truth) |
| `data/breakthroughs.csv` | Flat export for spreadsheets/pandas (`python3 build_csv.py`) |
| `data/breakthroughs.db` | SQLite mirror, auto-rebuilt when stale |

Schema highlights: `id, year, date, era, title, org, people[], paper, url,
what_it_broke, why_impossible, category, impact, surprise, tags[]`.

## Use it

```bash
# SQL over everything
python3 db.py

# CLI
python3 cli.py list --min-impact 9
python3 cli.py top --by surprise -n 5
python3 cli.py get 2016-alphago
python3 cli.py search "self-play"
python3 cli.py stats

# JSON API (http://127.0.0.1:8787)
python3 api.py
curl 'http://127.0.0.1:8787/top?by=combined&n=5'

# Static timeline site
open index.html
```

API endpoints: `/breakthroughs`, `/breakthroughs/<id>`, `/top?by=&n=`,
`/stats`, `/search?q=`.

## Contributing a breakthrough

Add an entry to `data/breakthroughs.json` with every field filled, cite the
primary paper, and justify your `impact`/`surprise` scores against the expert
consensus *of that entry's time* (that's what `why_impossible` is for).
Run `python3 build_csv.py && python3 db.py` so derived artifacts refresh.

## License

MIT.
