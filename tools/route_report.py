#!/usr/bin/env python3
"""v19 (§G6): offline route report over the FSSA server's ask records.

Reads ask_records.jsonl (and gap_log.jsonl when present) and reports,
per ask and in aggregate, which route the pipeline actually took:
wall time, cost, badge, the judges that sat, and the model that
served. This is the empirical half of "fastest route per question":
the server's seats already order themselves by measured latency
(§G2); this report shows, per question, what each route delivered,
so routing and budget decisions come from records, not impressions.

No network, no API calls, no cost. Usage:
    python3 route_report.py [ask_records.jsonl] [gap_log.jsonl] [out.md]
Defaults: ./ask_records.jsonl, ./gap_log.jsonl, ./route_report.md
"""
import json
import statistics
import sys
from collections import Counter, defaultdict

def load_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass
    return rows

def main():
    rec_path = sys.argv[1] if len(sys.argv) > 1 else "ask_records.jsonl"
    gap_path = sys.argv[2] if len(sys.argv) > 2 else "gap_log.jsonl"
    out_path = sys.argv[3] if len(sys.argv) > 3 else "route_report.md"

    records = load_jsonl(rec_path)
    asks = [r for r in records if r.get("kind") == "ask"]
    gaps = load_jsonl(gap_path)

    lines = ["# Route report (from ask records)", ""]
    lines.append(f"Ask records read: {len(asks)} "
                 f"(file: {rec_path}); gap-log rows: {len(gaps)}.")
    lines.append("")

    if asks:
        lines.append("## Per ask")
        lines.append("")
        lines.append("| When (UTC) | Wall s | Cost $ | Badge | Judges | Served | Question |")
        lines.append("|---|---|---|---|---|---|---|")
        import datetime
        for r in asks:
            when = datetime.datetime.fromtimestamp(
                r.get("ts", 0), datetime.timezone.utc).strftime("%m-%d %H:%M")
            judges = "+".join((r.get("panel") or {}).get("judges") or [])
            q = (r.get("question") or "").replace("|", "/")[:60]
            lines.append(
                f"| {when} | {r.get('wall_s', 0):.0f} | "
                f"{r.get('cost_usd', 0):.4f} | {r.get('badge')} | "
                f"{judges} | {(r.get('served') or '')[:28]} | {q} |")
        lines.append("")

        by_badge = defaultdict(list)
        for r in asks:
            by_badge[r.get("badge")].append(r)
        lines.append("## By badge")
        lines.append("")
        for badge, rs in sorted(by_badge.items()):
            walls = [r.get("wall_s", 0) for r in rs]
            costs = [r.get("cost_usd", 0) for r in rs]
            lines.append(
                f"- {badge}: {len(rs)} ask(s), median wall "
                f"{statistics.median(walls):.0f}s, median cost "
                f"${statistics.median(costs):.4f}")
        lines.append("")

        models = Counter()
        for r in asks:
            panel = r.get("panel") or {}
            for seat in ("third", "nim2"):
                m = (panel.get(seat) or {}).get("model")
                if m:
                    models[m] += 1
        if models:
            lines.append("## Judge seat models used")
            lines.append("")
            for m, n in models.most_common():
                lines.append(f"- {m}: {n}")
            lines.append("")

    if gaps:
        gates = Counter(g.get("gate") for g in gaps)
        lines.append("## IDK gates (gap log)")
        lines.append("")
        for gate, n in gates.most_common():
            lines.append(f"- {gate}: {n}")
        lines.append("")

    lines.append("Note: samples are the asks actually run on the "
                 "server. Route conclusions need volume; with a "
                 "handful of asks this report is a ledger, not a "
                 "verdict.")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {out_path} from {len(asks)} asks, {len(gaps)} gaps")

if __name__ == "__main__":
    main()
