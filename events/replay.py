"""Replay the event log: read every row back in append order, optionally stopping at a
month, and print what happened. No mutation -- replay only ever reads.

    python -m events.replay          # whole log
    python -m events.replay --month 3
"""
import argparse
from collections import Counter
from pathlib import Path

from events.log import DEFAULT_PATH, read_all


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", type=int, default=None, help="stop after this many distinct months")
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    args = parser.parse_args()

    events = read_all(Path(args.path))
    if args.month is not None:
        months_seen = []
        cutoff = len(events)
        for i, e in enumerate(events):
            ym = e["ts"][:7]
            if ym not in months_seen:
                months_seen.append(ym)
            if len(months_seen) > args.month:
                cutoff = i
                break
        events = events[:cutoff]

    counts = Counter(e["kind"] for e in events)
    print(f"{len(events)} events replayed: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for e in events:
        print(f"[{e['seq']:>5}] {e['ts']} {e['kind']:<12} {e['request_id'] or '-'}")


if __name__ == "__main__":
    main()
