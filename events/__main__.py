"""python -m events load        fresh data/events.db from the seed (clean + 3 demo holds)
python -m events replay 6    decide every request through month 6 (Oct), appending decisions
python -m events replay 6 --judge   same, with Jev (or the Claude fallback) answering first
python -m events nessie-setup       create the GC's bank account in the Nessie sandbox and load its history
python -m events nessie-sync        copy owner draws and sub payments from Nessie into the log
python -m events nessie-resolve ID  settle a payment whose outcome is unknown by asking Nessie
python -m events crs-verify [ID]    check vendors (all, or one) with CRS; record changed statuses
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
elif cmd == "nessie-setup":
    from kernel.executor import NessieError, nessie_setup
    data, _ = with_demo(generate())
    try:
        account = nessie_setup(data["ledger"])
    except NessieError as e:
        sys.exit(f"Nessie setup failed: {e}")
    print(f"Created the GC's Nessie account and loaded {len(data['ledger'])} ledger entries.")
    print(f"Add this line to .env:\nNESSIE_ACCOUNT_ID={account}")
elif cmd == "nessie-sync":
    from events.replay import sync_ledger
    from kernel.executor import NessieError, nessie_ledger
    if not PATH.exists():
        sys.exit(f"No log at {PATH}. Run `make events` first.")
    from events.replay import double_payments
    try:
        entries, skipped = nessie_ledger()
    except NessieError as e:
        sys.exit(f"Nessie sync failed: {e}")
    print(f"{len(entries)} ledger entries in Nessie, {sync_ledger(entries, path=PATH)} new to the log")
    for row in skipped:
        print(f"  skipped malformed row: {row}")
    for rid, n in double_payments(entries).items():
        print(f"  WARNING: Nessie shows {n} payments for {rid}")
elif cmd == "nessie-resolve":
    from events import log as events
    from kernel.executor import ExecutionRefused, NessieError, resolve_unknown
    rid = sys.argv[2] if len(sys.argv) > 2 else sys.exit("usage: python -m events nessie-resolve <request_id>")
    req = next((r for r in events.state(path=PATH)["pay_requests"] if r.id == rid), None)
    if req is None:
        sys.exit(f"no pay request {rid}")
    try:
        print(f"{rid}: {resolve_unknown(req, log_path=PATH)} (per Nessie)")
    except (ExecutionRefused, NessieError) as e:
        sys.exit(str(e))
elif cmd == "crs-verify":
    import crs
    from events import log as events
    which = sys.argv[2] if len(sys.argv) > 2 else None
    vendors = [v for v in events.state(path=PATH)["vendors"] if which in (None, v.id)]
    if not vendors:
        sys.exit(f"no vendor {which}")
    for v in vendors:
        updated, result, error = crs.check_vendor(v)
        if error:
            print(f"  {v.id}: check failed, status left as {v.crs_status!r}: {error}")
            continue
        if updated.crs_status != v.crs_status:
            events.append("fact", updated, table="vendors", path=PATH)
        print(f"  {v.id}: {result.status} ({'; '.join(result.reasons)})")
else:
    sys.exit(__doc__)
