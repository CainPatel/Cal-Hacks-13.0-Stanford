"""python -m events load        fresh data/events.db from the seed (clean + 3 demo holds)
python -m events replay 6    decide every request through month 6 (Oct), appending decisions
python -m events replay 6 --judge   same, with Jev (or the Claude fallback) answering first
"""
import sys
from collections import Counter
from pathlib import Path

import pipeline
from events.replay import load_seed, replay
from seed.anomalies import with_demo
from seed.generate import generate

PATH = Path("data/events.db")

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "load":
    if PATH.exists():  # never wipe a log that may hold real decisions and signatures
        sys.exit(f"{PATH} already exists. Delete it yourself to start a fresh log.")
    data, _ = with_demo(generate())
    print(f"loaded {load_seed(data, path=PATH)} facts into {PATH}")
elif cmd == "replay":
    args = [a for a in sys.argv[2:] if not a.startswith("--")]
    month = int(args[0]) if args else 6
    if not PATH.exists():
        sys.exit(f"No log at {PATH}. Run `make events` first.")
    judge = None
    if "--judge" in sys.argv:
        from judgment import judge
    decisions = replay(month, path=PATH, judge=judge)
    print(f"kernel: {pipeline.KERNEL}")
    print(f"{len(decisions)} new decisions through month {month}: {dict(Counter(d.action.value for d in decisions))}")
else:
    sys.exit(__doc__)
