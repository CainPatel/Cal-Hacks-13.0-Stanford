# Approval key wire contract (firmware/ ESP32 and device/ Raspberry Pi)

Both devices speak exactly this, so `kernel/executor.py` doesn't care which one is on the desk.

## Payload
The executor sends canonical JSON: `json.dumps(payload, sort_keys=True, separators=(",", ":"))`, UTF-8.
Fields: `request_id`, `payee`, `amount` (string, e.g. `"38000.00"`), `job`, `nonce` (hex string), `expires_at` (unix seconds).
The device signs the **exact bytes it received**. The executor verifies against the exact bytes it sent; never re-serialise.

## Signature
ECDSA P-256 over SHA-256. Returned as hex of raw `r || s` (64 bytes, 128 hex chars), not DER.
- Approve: signed message = body bytes.
- Reject: signed message = `b"REJECT" + body bytes`.

## Endpoints
| | |
|---|---|
| `GET /pubkey` | Hex of the uncompressed point `04 || X || Y`. Goes in `config/contract.yaml`. Pi only: `?format=pem` gives PEM. |
| `POST /approve` | Body as above. `200 {"sig", "nonce"}` approve · `200 {"rejected": true, "sig"}` reject · `408 {"error":"timeout"}` after 90 s with no press · `409 {"error":"busy"}` if a different payment is already waiting. ESP32: holds the request until a button. Pi: answers in rounds (below); also `400` for bad JSON, missing fields, or already expired. Pi alias: `POST /request`. |
| `GET /` | Phone approval page. ESP32 serves it on `:8080`; the Pi serves it on the same port as the API. Pi with `DRAWKEY_BUTTONS_ONLY=1` (set by the systemd service): read-only, no buttons, "Press the green or red button on the key." |
| `POST /decide/{approve\|reject}` | Pi only: the page's buttons. `200 {"ok": true}`. `403` and no effect when `DRAWKEY_BUTTONS_ONLY=1`, so only a physical press decides. |

The executor still owns nonce-replay and expiry checks.

### Rounds (Pi)

A request held open for 90 s through Tailscale Funnel and the API can be dropped, and then a press is lost
(the key signs, Draw never hears) and the key stays busy until its wait runs out. So the Pi answers in rounds:

- It holds each `POST /approve` for at most 20 s, then answers `202 {"waiting": true, "nonce"}`. The executor
  sends the **same bytes** again, and retries a dropped connection, until it gets 200/408/409/400 or 100 s pass.
- The same nonce with the same bytes rejoins the payment already waiting (never `409`). The same nonce with
  different bytes is refused (`409`), so one nonce never signs two contents.
- An answer already given is kept for 10 minutes and repeated for a resend of the same nonce, so a response
  lost on the way back is recovered on the next round. A press between rounds counts.
- If nobody asks about the waiting payment for 45 s, the server gave up: the key frees itself for the next one.

`kernel/executor.py`'s `request_device_approval` speaks both: an ESP32 that holds the request works unchanged.

Reference client: `firmware/check_device.py <host[:port]>` (works against both).
