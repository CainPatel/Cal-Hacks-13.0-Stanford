"""Draw approval key on a Raspberry Pi. Same wire contract as firmware/approval_key (see docs/device-api.md).

    uvicorn device.main:app --host 0.0.0.0 --port 8000        # on the Pi
    DRAWKEY_MOCK=1 uvicorn device.main:app --port 8000        # on a Mac: type a / r + Enter
"""
import json
import os
import sys
import threading
import time
import tomllib
from pathlib import Path

import anyio
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from gpiozero import LED, Button, Device

CFG = tomllib.loads((Path(__file__).parent / "config.toml").read_text())
MOCK = os.environ.get("DRAWKEY_MOCK") == "1"
BUTTONS_ONLY = os.environ.get("DRAWKEY_BUTTONS_ONLY") == "1"  # Pi service: only the GPIO buttons decide
FIELDS = {"request_id", "payee", "amount", "job", "nonce", "expires_at"}


def load_key() -> ec.EllipticCurvePrivateKey:
    path = Path.home() / ".drawkey" / "private.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), None)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    return key


KEY = load_key()
PUB = KEY.public_key()
PUB_HEX = PUB.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint).hex()
print("PUBKEY", PUB_HEX, flush=True)


def sign(msg: bytes) -> str:
    r, s = decode_dss_signature(KEY.sign(msg, ec.ECDSA(hashes.SHA256())))
    return (r.to_bytes(32) + s.to_bytes(32)).hex()  # r||s, same as the ESP32


if MOCK:
    from gpiozero.pins.mock import MockFactory
    Device.pin_factory = MockFactory()
approve_btn = Button(CFG["approve_pin"], pull_up=True)
reject_btn = Button(CFG["reject_pin"], pull_up=True)
green, red = LED(CFG["green_led"]), LED(CFG["red_led"])

# One payment waits for a button at a time. The server asks in short rounds (HOLD seconds each) with
# the same bytes until it gets an answer, so a dropped connection or a slow proxy never loses a press:
# the next round picks the payment up, and an answer already given is kept (ANSWERED) for a resend.
HOLD = 20       # longest one request is held before "still waiting" (202)
STALE = 45      # no round for this long: the server gave up, so the key frees itself
KEEP = 600      # how long a signed answer is kept for a resend of the same nonce
state = threading.Lock()  # guards `current` and ANSWERED; never held while waiting
current: dict | None = None  # {"p", "body", "decided": Event, "decision", "deadline", "last_seen"}
ANSWERED: dict[str, tuple[float, int, dict]] = {}  # nonce -> (when, status, response body)
pending: dict | None = None  # what the page shows: the waiting payment, or None


def decide(what: str) -> None:
    with state:
        if current and not current["decided"].is_set():
            current["decision"] = what
            current["decided"].set()


approve_btn.when_pressed = lambda: decide("approve")
reject_btn.when_pressed = lambda: decide("reject")

if MOCK:
    def watch(led, name):
        last = None
        while True:
            if led.value != last:
                last = led.value
                print(f"[LED] {name} {'ON' if last else 'off'}", flush=True)
            time.sleep(0.05)

    def keyboard():
        for line in sys.stdin:  # a tty, or a pipe for scripted demos
            btn = {"a": approve_btn, "r": reject_btn}.get(line.strip().lower())
            if btn:
                btn.pin.drive_low(); time.sleep(0.05); btn.pin.drive_high()

    for led, name in ((green, "green"), (red, "red")):
        threading.Thread(target=watch, args=(led, name), daemon=True).start()
    if "pytest" not in sys.modules:
        threading.Thread(target=keyboard, daemon=True).start()
        print("Mock mode: type a + Enter to approve, r + Enter to reject.", flush=True)

app = FastAPI()


def flash(led: LED) -> None:
    green.off(); red.off()
    led.blink(on_time=3, n=1)  # on for 3 s, then off, like the ESP32


@app.get("/pubkey", response_class=PlainTextResponse)
def pubkey(format: str = "hex"):
    if format == "pem":
        return PUB.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return PUB_HEX


@app.post("/approve")
@app.post("/request")
async def approve(req: Request):
    body = await req.body()  # sign these exact bytes, never a re-serialisation
    try:
        p = json.loads(body)
        assert isinstance(p, dict) and FIELDS <= p.keys()
    except Exception:
        return JSONResponse({"error": f"body must be JSON with {sorted(FIELDS)}"}, 400)
    with state:
        answered = ANSWERED.get(p["nonce"])
    if answered:  # the answer went out but never arrived: same bytes, same answer
        return JSONResponse(answered[2], answered[1])
    if float(p["expires_at"]) < time.time():
        return JSONResponse({"error": "expired"}, 400)
    started = claim(p, body)
    if started is None:
        return JSONResponse({"error": "busy"}, 409)
    if started is False:
        return JSONResponse({"error": "this nonce is already waiting with different contents"}, 409)
    return await anyio.to_thread.run_sync(wait_for_press, p["nonce"])


def claim(p: dict, body: bytes):
    """Make this payment the one waiting, or rejoin it. None: another payment is waiting; False: the
    same nonce with different bytes (never re-sign different contents under one nonce); else True."""
    global current, pending
    now = time.time()
    with state:
        for n in [n for n, (t, _, _) in ANSWERED.items() if now - t > KEEP]:
            del ANSWERED[n]
        if current and current["p"]["nonce"] == p["nonce"]:
            if current["body"] != body:
                return False
            current["last_seen"] = now
            return True
        if current and now - current["last_seen"] < STALE and now < current["deadline"]:
            return None
        if current:
            print(f"Gave up on {current['p']['request_id']}: the server stopped asking.", flush=True)
        current = {"p": p, "body": body, "decided": threading.Event(), "decision": None, "last_seen": now,
                   "deadline": min(float(p["expires_at"]), now + CFG["wait_seconds"])}
        pending = p
    red.off()
    green.blink(on_time=0.25, off_time=0.25)
    print(f"Waiting for a button: {p['payee']} ${p['amount']} ({p['job']})", flush=True)
    return True


def wait_for_press(nonce: str) -> JSONResponse:
    """One round: wait up to HOLD seconds, then answer, time out, or say "still waiting" (202)."""
    global current, pending
    with state:
        mine = current if current and current["p"]["nonce"] == nonce else None
    if mine is None:  # answered or abandoned between claim() and here
        with state:
            answered = ANSWERED.get(nonce)
        return JSONResponse(answered[2], answered[1]) if answered else JSONResponse({"error": "timeout"}, 408)
    got = mine["decided"].wait(max(0.0, min(HOLD, mine["deadline"] - time.time())))
    with state:
        if nonce in ANSWERED:  # another round for the same nonce already answered
            _, status, content = ANSWERED[nonce]
            return JSONResponse(content, status)
        if not got and time.time() < mine["deadline"]:
            mine["last_seen"] = time.time()
            return JSONResponse({"waiting": True, "nonce": nonce}, 202)
        if got and mine["decision"] == "approve":
            status, content = 200, {"sig": sign(mine["body"]), "nonce": nonce}
        elif got:
            status, content = 200, {"rejected": True, "sig": sign(b"REJECT" + mine["body"])}
        else:
            status, content = 408, {"error": "timeout"}
        ANSWERED[nonce] = (time.time(), status, content)
        if current is mine:
            current, pending = None, None
    if status == 408:
        green.off()
    else:
        flash(green if "rejected" not in content else red)
    return JSONResponse(content, status)


@app.get("/pending")
def get_pending():
    return pending or {}


@app.post("/decide/{what}")
def decide_page(what: str):
    if BUTTONS_ONLY:  # the page is reachable from the internet; a press on the key is the only approval
        return JSONResponse({"error": "press the button on the key"}, 403)
    if what not in ("approve", "reject"):
        return JSONResponse({"error": "approve or reject"}, 400)
    decide(what)
    return {"ok": True}


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Draw approval key</title>
<style>
body{font-family:system-ui,sans-serif;background:#faf8f5;color:#222;text-align:center;margin:0;padding:10vh 16px}
#amt{font-size:clamp(48px,16vw,120px);font-weight:700;margin:.2em 0}
#payee{font-size:28px} #job{color:#666;margin-bottom:2em}
button{font-size:28px;padding:24px 0;width:min(45%,240px);border:0;border-radius:16px;color:#fff;margin:6px}
#yes{background:#1a7f37} #no{background:#c62828}
</style>
<div id=wait>Nothing to approve right now.</div>
<div id=req hidden><div id=payee></div><div id=amt></div><div id=job></div>
<!--decide--></div>
<script>
async function go(w){await fetch('/decide/'+w,{method:'POST'});tick()}
async function tick(){const p=await (await fetch('/pending')).json();const has=!!p.payee;
wait.hidden=has;req.hidden=!has;if(has){payee.textContent=p.payee;job.textContent=p.job;
amt.textContent='$'+Number(p.amount).toLocaleString('en-US',{minimumFractionDigits:2})}}
tick();setInterval(tick,1000)
</script>"""


BUTTONS = """<button id=yes onclick="go('approve')">Approve</button><button id=no onclick="go('reject')">Reject</button>"""


@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE.replace("<!--decide-->", "<p>Press the green or red button on the key.</p>" if BUTTONS_ONLY else BUTTONS)
