"""Payments across processes: what several Vercel instances (or local workers) look like to the log."""
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import pipeline
from events import log as events
from events.replay import load_seed, replay
from seed.anomalies import with_demo
from seed.generate import generate

pytestmark = pytest.mark.skipif(pipeline.kernel_decide is None, reason="needs kernel/")
ROOT = Path(__file__).resolve().parents[1]

CHILD = textwrap.dedent("""
    import json, os, sys, time
    from decimal import Decimal
    from types import SimpleNamespace
    sys.path.insert(0, {root!r})
    from events import log as events
    from kernel import executor as ex
    from shared.schema import Decision
    log, bills, start = sys.argv[1], sys.argv[2], float(sys.argv[3])

    def fake(method, url, params=None, json=None, timeout=None):
        if method == "POST":
            with open(bills, "a") as f:
                f.write("bill\\n")
            time.sleep(0.3)  # a slow bank, so overlapping attempts would both get in without a lock
        return SimpleNamespace(status_code=201, text="", json=lambda: {{"objectCreated": {{"_id": str(os.getpid())}}}})

    ex.requests.request = fake
    os.environ.update(NESSIE_API_KEY="k", NESSIE_ACCOUNT_ID="a")
    state = events.state(path=log)
    req = next(r for r in state["pay_requests"] if r.id == "pr_sub_02_6")
    d = next(Decision.model_validate(e.data) for e in events.read(kind="kernel_trace", path=log) if e.request_id == req.id)
    while time.time() < start:
        time.sleep(0.005)
    try:
        ex.execute(d, req, auto_release_limit_usd=Decimal("25000"), log_path=log)
        print("paid")
    except ex.ExecutionRefused as e:
        print("refused:", e)
""")


def test_separate_processes_pay_a_request_once(tmp_path):
    log, bills = tmp_path / "events.db", tmp_path / "bills.txt"
    load_seed(with_demo(generate())[0], path=log)
    replay(6, path=log)
    script = tmp_path / "child.py"
    script.write_text(CHILD.format(root=str(ROOT)))
    start = time.time() + 2.0
    procs = [subprocess.Popen([sys.executable, str(script), str(log), str(bills), str(start)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
    outs = [p.communicate(timeout=60) for p in procs]
    results = [o.strip().splitlines()[-1] if o.strip() else f"ERROR {e[-300:]}" for o, e in outs]
    assert sorted(r.split(":")[0] for r in results) == ["paid"] + ["refused"] * 5, results
    assert bills.read_text().count("bill") == 1


def test_file_lock_excludes_another_process(tmp_path):
    log = tmp_path / "events.db"
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from events import log as events
        with events.exclusive("payments", path={str(log)!r}):
            print("held", flush=True)
            time.sleep(1.5)
    """)], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    t = time.monotonic()
    with events.exclusive("payments", path=log):
        waited = time.monotonic() - t
    holder.wait(timeout=10)
    assert waited > 1.0


def test_lock_wait_has_a_limit(tmp_path):
    log = tmp_path / "events.db"
    with events.exclusive("payments", path=log):
        holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from events import log as events
            try:
                with events.exclusive("payments", path={str(log)!r}, timeout_s=0.5):
                    print("got it")
            except events.LockTimeout:
                print("timed out")
        """)], stdout=subprocess.PIPE, text=True)
        out, _ = holder.communicate(timeout=10)
    assert out.strip() == "timed out"
