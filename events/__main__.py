"""python -m events load        fresh data/events.db from the seed (clean + 3 demo holds)
python -m events replay 6    decide every request through month 6 (Oct), appending decisions
python -m events replay 6 --judge   same, with Jev (or the Claude fallback) answering first
python -m events replay 6 --judge --redo   decide every unpaid request again (the newest decision counts)
python -m events nessie-setup       create the GC's bank account in the Nessie sandbox and load its history
python -m events nessie-sync        copy owner draws and sub payments from Nessie into the log
python -m events nessie-resolve ID  settle a payment whose outcome is unknown by asking Nessie
python -m events crs-verify [ID]    check vendors (all, or one) with CRS; record changed statuses
python -m events snapshot [FILE]    copy the shared Postgres log into a new SQLite file (default data/events.db)
python -m events archive --yes      set the whole log aside (renamed, never deleted) and start an empty one
python -m events reset --yes        start over: set the log aside, load the seed, sync the bank, decide May-Oct

`load` and `reset` include the demo company whose pay application arrives by email (seed/demo_company.py).

With DATABASE_URL set (in .env), every command works on the shared Postgres log, the same one the
deployment uses; without it, on data/events.db.
"""
import sys
from collections import Counter
from pathlib import Path

import pipeline
from events import log as events
from events.replay import load_seed, replay
from seed import demo_company
from seed.anomalies import with_demo
from seed.generate import generate

PATH = Path("data/events.db")

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "load":
    if not events.is_empty(PATH):  # never wipe a log that may hold real decisions and signatures
        sys.exit(f"{events.where(PATH)} already has events. Start a fresh log yourself (delete the file or the table).")
    data = demo_company.add(with_demo(generate())[0])
    print(f"loaded {load_seed(data, path=PATH)} facts into {events.where(PATH)}")
elif cmd == "reset":
    import os
    if "--yes" not in sys.argv:
        sys.exit(f"This sets aside everything in {events.where(PATH)} (kept, not deleted), loads the demo company and "
                 "decides May-October. Run again with --yes to do it.")
    if not events.is_empty(PATH):
        print(f"the old log is now {events.archive(PATH)}")
    data = demo_company.add(with_demo(generate())[0])
    print(f"loaded {load_seed(data, path=PATH)} facts into {events.where(PATH)}")
    if os.environ.get("NESSIE_API_KEY") and os.environ.get("NESSIE_ACCOUNT_ID"):
        from events.replay import sync_ledger
        from kernel.executor import NessieError, nessie_ledger
        try:
            print(f"bank: {sync_ledger(nessie_ledger()[0], path=PATH)} ledger entries new to the log")
        except NessieError as e:
            print(f"bank: not synced ({e})")
    from judgment.jev import JevError, _config as jev_config
    try:
        jev_config()
        has_model = True
    except JevError:
        has_model = bool(os.environ.get("ANTHROPIC_API_KEY"))
    judge = None
    if has_model:  # with no model at all, judging would only escalate everything (rule 15 sees zero confidence)
        from judgment import judge
    decisions = replay(6, path=PATH, judge=judge)
    print(f"{len(decisions)} decisions through October ({'with' if judge else 'without'} judgment): "
          f"{dict(Counter(d.action.value for d in decisions))}")
elif cmd == "replay":
    args = [a for a in sys.argv[2:] if not a.startswith("--")]
    month = int(args[0]) if args else 6
    if events.is_empty(PATH):
        sys.exit(f"No events in {events.where(PATH)}. Run `make events` first.")
    judge = None
    if "--judge" in sys.argv:
        from judgment import judge
    decisions = replay(month, path=PATH, judge=judge, redo="--redo" in sys.argv)
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
    if events.is_empty(PATH):
        sys.exit(f"No events in {events.where(PATH)}. Run `make events` first.")
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
elif cmd == "snapshot":
    import os
    if not os.environ.get("DATABASE_URL"):
        sys.exit("DATABASE_URL isn't set, so there's no shared log to copy; data/events.db is already local.")
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else PATH
    try:
        n = events.copy_to_sqlite(dst)
    except FileExistsError as e:
        sys.exit(f"{e} (`make snapshot` archives it for you)")
    print(f"copied {n} events from {events.where(PATH)} into {dst}; run it offline with `make offline`")
elif cmd == "archive":
    if "--yes" not in sys.argv:
        sys.exit(f"This sets aside everything in {events.where(PATH)} (kept, not deleted) and starts an empty log. "
                 "Run again with --yes to do it.")
    if events.is_empty(PATH):
        sys.exit(f"{events.where(PATH)} is already empty; nothing to set aside.")
    print(f"the old log is now {events.archive(PATH)}; {events.where(PATH)} is empty")
else:
    sys.exit(__doc__)
