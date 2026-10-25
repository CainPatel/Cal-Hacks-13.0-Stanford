"""Check that every stage of a running Draw works, end to end, over HTTP: local or deployed.

    python scripts/smoke.py                                    # localhost:8000 + localhost:3000
    python scripts/smoke.py --api https://drawapi.vercel.app --web https://drawweb.vercel.app
    python scripts/smoke.py --sync --pay --approve             # also the steps that write (see below)

By default it only reads (the attack console runs in memory and writes nothing). Opt in to more:
  --sync     POST /sync and POST /replay: copy the bank's ledger in, decide anything undecided
  --pay      pay the smallest ready October request through Nessie (sandbox money), then check it
             shows as paid and that paying it again is refused
  --approve  send the smallest October request that needs the owner's OK to the approval key;
             someone presses green within 90 seconds

Prints one line per check and exits 1 if any check failed. Never prints a secret: the API's
status page only answers yes/no.
"""
import argparse
import json
import subprocess
import sys

import requests

RESULTS = {"PASS": 0, "WARN": 0, "FAIL": 0}


def say(kind: str, what: str, detail: str = "") -> None:
    RESULTS[kind] += 1
    print(f"{kind:4}  {what}" + (f": {detail}" if detail else ""))


def check(what: str, ok: bool, detail: str = "", warn: bool = False) -> bool:
    say("PASS" if ok else ("WARN" if warn else "FAIL"), what, detail)
    return ok


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def get(self, path: str, **kw):
        return requests.get(f"{self.base}{path}", timeout=kw.pop("timeout", 60), **kw)

    def post(self, path: str, **kw):
        return requests.post(f"{self.base}{path}", timeout=kw.pop("timeout", 120), **kw)


def status(api: Api) -> dict | None:
    try:
        r = api.get("/")
    except requests.RequestException as e:
        check("API reachable", False, f"{api.base}: {type(e).__name__}")
        return None
    if not check("API reachable", r.ok, f"{api.base} answered {r.status_code}"):
        return None
    s = r.json()
    check("event log connected", s.get("log_has_events") is True, f"{s.get('event_log')}, has events: {s.get('log_has_events')}")
    c = s.get("configured", {})
    for key, why in [("anthropic_key", "perception's Claude fallback, video"), ("jev_key", "Jev judgment"),
                     ("nessie_key", "payments"), ("nessie_account", "payments"), ("crs", "vendor identity (rule 09)"),
                     ("device_url", "the approval key"), ("approval_pubkey", "verifying the key's signatures")]:
        if key in c:
            check(f"configured: {key}", bool(c[key]), why)
    if "judgment" in c:
        check("requests reviewed by Jev", c["judgment"] == "jev", f"judgment = {c['judgment']}", warn=c["judgment"] == "claude")
    if s.get("bank_fingerprint") is not None or "bank_fingerprint" in s:
        say("PASS" if s.get("bank_fingerprint") else "WARN", "bank account fingerprint",
            f"{s.get('bank_fingerprint')} (must match on every copy that pays)")
        say("PASS", "event log fingerprint", f"{s.get('log_fingerprint') or 'local SQLite'} (equal = same log)")
    else:
        say("WARN", "status page is older than this script", "redeploy the API from main")
    try:
        head = subprocess.run(["git", "rev-parse", "--short=7", "origin/main"], capture_output=True, text=True).stdout.strip()
    except OSError:
        head = ""
    if s.get("version") and head:
        check("deployed commit is origin/main", s["version"] == head, f"deployed {s['version']}, origin/main {head}",
              warn=True)
    return s


def requests_and_detail(api: Api) -> list[dict]:
    r = api.get("/requests", params={"month": 6})
    if not check("pay requests listed (October)", r.ok and len(r.json()) > 0, f"{len(r.json()) if r.ok else r.status_code} rows"):
        return []
    rows = r.json()
    valid = {"ready", "needs_ok", "on_hold", "not_checked", "paid"}
    counts = {s: sum(1 for x in rows if x["status"] == s) for s in valid}
    check("every row has a status and a sentence", all(x["status"] in valid and x["sentence"] for x in rows),
          ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    check("every request has been checked", counts["not_checked"] == 0,
          f"{counts['not_checked']} not checked (press Check on the dashboard, or --sync)", warn=True)
    held = next((x for x in rows if x["status"] == "on_hold"), None)
    if held:
        d = api.get(f"/requests/{held['id']}").json()
        check("a hold shows every check and why", len(d.get("checklist", [])) == 15 and d["sentence"].startswith("On hold"),
              f"{held['id']}: {d['sentence'][:90]}")
    decided = next((x for x in rows if x["status"] in ("ready", "needs_ok", "on_hold")), None)
    if decided:
        d = api.get(f"/requests/{decided['id']}").json()
        if "judgment" in d:
            j = d["judgment"]
            check("Jev reviewed the latest decision", bool(j) and j.get("source") == "jev",
                  f"{decided['id']}: " + (f"{j['source']} suggested {j['proposal']['action']}" if j else
                                         "decided on rules alone; POST /replay?redo=true puts Jev in the loop"), warn=True)
    return rows


def log_and_stream(api: Api) -> None:
    r = api.get("/log", params={"limit": 5})
    if r.status_code == 404:
        check("event log endpoint (GET /log)", False, "404: the API is older than main; redeploy it")
        return
    events = r.json().get("events", []) if r.ok else []
    seqs = [e["seq"] for e in events]
    check("event log, newest first, a sentence per row", r.ok and seqs == sorted(seqs, reverse=True) and all(e["line"] for e in events),
          events[0]["line"][:90] if events else str(r.status_code))
    check("event log filters", api.get("/log", params={"kind": "nope"}).status_code == 422
          and api.get("/log", params={"kind": "kernel_trace", "limit": 1}).ok)
    if seqs:
        body = api.get("/events", params={"after": seqs[0] - 3, "follow": "false"}).text
        # on Vercel a "retry:" block comes first (a 10 s poll, not a held connection), so count only events
        n = sum(b.startswith("id: ") for b in body.split("\n\n"))
        check("live stream (Server-Sent Events)", n == 3, f"{n} events after #{seqs[0] - 3}")


def security(api: Api) -> None:
    r = api.get("/scorecard", timeout=120)
    if check("scorecard", r.ok, "" if r.ok else str(r.status_code)):
        c = r.json()
        check("no attack paid out", c["wrongful_releases"] == 0,
              f"{c['cases']} cases, caught by rules {c['rule_catch_rate']:.1%}, false holds {c['false_hold_rate']:.1%}")
    r = api.get("/calibration")
    check("calibration report", r.ok and r.json().get("answers", 0) > 0,
          f"{r.json()['answers']} Jev answers, ECE {r.json()['curve']['expected_calibration_error']}" if r.ok else str(r.status_code))
    names = {a["name"] for a in api.get("/attacks").json()}
    out = api.post("/attacks", json={"name": "bank_change_email", "target": "sub_02", "settings": {"homoglyph": True}})
    check("attack console catches a lookalike-domain bank change", out.ok and out.json().get("caught") is True,
          f"{len(names)} attacks offered" if out.ok else out.text[:120])
    v = api.get("/vendors")
    check("subcontractor list (Upload screen)", v.ok and len(v.json().get("vendors", [])) > 0,
          f"{len(v.json()['vendors'])} subs" if v.ok else f"{v.status_code}: redeploy the API from main")


def web(base: str, api_base: str) -> None:
    base = base.rstrip("/")
    for page in ["/", "/approve", "/security", "/upload", "/log"]:
        try:
            r = requests.get(f"{base}{page}", timeout=60)
            check(f"dashboard page {page}", r.ok, str(r.status_code) + ("" if r.ok or page != "/log" else ": redeploy the dashboard"))
        except requests.RequestException as e:
            check(f"dashboard page {page}", False, type(e).__name__)
            return
    try:
        cfg = requests.get(f"{base}/api/blob-upload", timeout=30).json()
        deployed = not base.startswith(("http://localhost", "http://127.0.0.1"))
        check("uploads to the private Blob store", cfg.get("enabled") or not deployed,
              "on" if cfg.get("enabled") else "off (local uploads go straight to the API)")
    except (requests.RequestException, ValueError) as e:
        check("upload settings", False, type(e).__name__)
    pre = requests.options(f"{api_base.rstrip('/')}/requests", timeout=30, headers={
        "Origin": base, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    check("the API accepts this dashboard (CORS)", pre.headers.get("access-control-allow-origin") == base,
          pre.headers.get("access-control-allow-origin") or f"no allow-origin for {base}: set CORS_ORIGINS on the API")


def sync(api: Api) -> None:
    r = api.post("/sync")
    check("bank sync (POST /sync)", r.ok, json.dumps(r.json())[:160] if r.ok else r.text[:160])
    if r.ok and r.json().get("paid_twice"):
        say("FAIL", "the bank shows a request paid twice", json.dumps(r.json()["paid_twice"]))
    r = api.post("/replay", params={"through": 6}, timeout=300)
    check("check undecided requests (POST /replay)", r.ok,
          f"{r.json()['new_decisions']} new, Jev {'on' if r.json().get('judged') else 'off'}" if r.ok else r.text[:160])


def pay(api: Api, rows: list[dict]) -> None:
    ready = sorted((x for x in rows if x["status"] == "ready"), key=lambda x: float(x["amount"]))
    if not ready:
        say("WARN", "pay", "no ready October request left to pay")
        return
    rid = ready[0]["id"]
    r = api.post(f"/requests/{rid}/pay", json={"confirm": True}, timeout=200)
    check(f"pay {rid} (${float(ready[0]['amount']):,.2f}, Nessie sandbox)", r.ok and r.json().get("paid") is True, r.text[:160])
    after = api.get(f"/requests/{rid}").json()
    check("it shows as paid", after["status"] == "paid", after["sentence"])
    again = api.post(f"/requests/{rid}/pay", json={"confirm": True})
    check("paying it again is refused", again.status_code == 409, again.json().get("detail", "")[:120])


def approve(api: Api, rows: list[dict]) -> None:
    waiting = sorted((x for x in rows if x["status"] == "needs_ok"), key=lambda x: float(x["amount"]))
    if not waiting:
        say("WARN", "approve", "no October request needs the owner's OK")
        return
    rid = waiting[0]["id"]
    print(f"      press GREEN on the approval key now: {waiting[0]['sub']}, ${float(waiting[0]['amount']):,.2f} (90 s)")
    r = api.post(f"/requests/{rid}/approve", timeout=150)
    out = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check(f"approve {rid} on the key, then pay", r.ok and out.get("paid") is True, json.dumps(out)[:160] or r.text[:160])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--web", default="http://localhost:3000")
    p.add_argument("--sync", action="store_true")
    p.add_argument("--pay", action="store_true")
    p.add_argument("--approve", action="store_true")
    a = p.parse_args()
    api = Api(a.api)
    print(f"Draw smoke check: API {a.api}, dashboard {a.web}\n")
    if status(api) is None:
        print("\nThe API isn't answering; nothing else can be checked.")
        return 1
    if a.sync:
        sync(api)
    rows = requests_and_detail(api)
    log_and_stream(api)
    security(api)
    web(a.web, a.api)
    if a.pay:
        pay(api, rows)
    if a.approve:
        approve(api, api.get("/requests", params={"month": 6}).json())
    print(f"\n{RESULTS['PASS']} passed, {RESULTS['WARN']} warnings, {RESULTS['FAIL']} failed")
    return 1 if RESULTS["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
