"""Minimal client for Jev's System One endpoint.

Default route is Vercel AI Gateway (no waitlist): base URL
https://ai-gateway.vercel.sh/typesafe, key in JEV_API_KEY (or AI_GATEWAY_API_KEY), model
typesafe-ai/jev. Set JEV_BASE_URL=https://thejevai.com with JEV_API_KEY for
direct access. Both serve the same request; the response envelopes differ and
both are accepted.
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

GATEWAY_URL = "https://ai-gateway.vercel.sh/typesafe"
TIMEOUT_S = 20
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
RETRIES = 2


class JevError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would replay a redirected POST as a GET to any host, Authorization header included."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"refusing redirect to {newurl}", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _config() -> tuple[str, str, str]:
    base = os.environ.get("JEV_BASE_URL", GATEWAY_URL).rstrip("/")
    if not base.startswith("https://"):
        raise JevError("JEV_BASE_URL must be https")
    via_gateway = urllib.parse.urlsplit(base).hostname == "ai-gateway.vercel.sh"
    # The team stores the key as JEV_API_KEY; via the gateway an AI_GATEWAY_API_KEY wins if both are set.
    names = ("AI_GATEWAY_API_KEY", "JEV_API_KEY") if via_gateway else ("JEV_API_KEY",)
    key = next((os.environ[n] for n in names if os.environ.get(n)), None)
    if not key:
        raise JevError(f"no Jev API key configured (looked for {' or '.join(names)} for {base})")
    model = os.environ.get("JEV_MODEL", "typesafe-ai/jev" if via_gateway else "typesafe/jev-1.14")
    return f"{base}/v1/systemone", key, model


def _answers(raw: bytes) -> dict:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise JevError(f"response is not JSON: {e}") from e
    if isinstance(body, dict) and isinstance(body.get("answers"), dict):  # AI Gateway
        return body["answers"]
    data = body.get("data") if isinstance(body, dict) else None
    result = data.get("result") if isinstance(data, dict) else None
    if isinstance(body, dict) and body.get("code") == 0 and isinstance(result, dict) and isinstance(result.get("answers"), dict):  # direct
        return result["answers"]
    raise JevError(f"unexpected response shape: {type(body).__name__}")


def ask(state: dict, questions: dict, *, opener=_OPENER.open, sleep=time.sleep) -> dict:
    """POST state + questions; return the raw answers keyed by question id."""
    url, key, model = _config()
    payload = json.dumps({"model": model, "state": state, "questions": questions}).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            with opener(request, timeout=TIMEOUT_S) as resp:
                raw = resp.read()
            return _answers(raw)
        except urllib.error.HTTPError as e:
            detail = (e.read() or b"")[:300].decode(errors="replace") if e.fp else ""
            hint = f" (sent to {url}; is the key for this route?)" if e.code in (401, 403) else ""
            last = JevError(f"HTTP {e.code}: {detail}{hint}")
            if e.code not in RETRY_STATUSES:
                break
        except (OSError, http.client.HTTPException) as e:  # URLError, timeouts, resets, truncated reads
            last = JevError(f"{type(e).__name__}: {e}")
        if attempt < RETRIES:
            sleep(2**attempt)
    raise last  # type: ignore[misc]
