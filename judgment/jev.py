"""Minimal client for Jev's System One endpoint.

Default route is Vercel AI Gateway (no waitlist): base URL
https://ai-gateway.vercel.sh/typesafe, key in AI_GATEWAY_API_KEY, model
typesafe-ai/jev. Set JEV_BASE_URL=https://thejevai.com with JEV_API_KEY for
direct access. Both serve the same request; the response envelopes differ and
both are accepted.
"""

import json
import os
import time
import urllib.error
import urllib.request

GATEWAY_URL = "https://ai-gateway.vercel.sh/typesafe"
TIMEOUT_S = 20
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
RETRIES = 2


class JevError(RuntimeError):
    pass


def _config() -> tuple[str, str, str]:
    base = os.environ.get("JEV_BASE_URL", GATEWAY_URL).rstrip("/")
    via_gateway = "ai-gateway.vercel.sh" in base
    key = os.environ.get("AI_GATEWAY_API_KEY" if via_gateway else "JEV_API_KEY")
    if not key:
        raise JevError("no Jev API key configured")
    model = os.environ.get("JEV_MODEL", "typesafe-ai/jev" if via_gateway else "typesafe/jev-1.14")
    return f"{base}/v1/systemone", key, model


def _answers(body: dict) -> dict:
    if isinstance(body.get("answers"), dict):  # AI Gateway
        return body["answers"]
    data = body.get("data") or {}
    result = data.get("result") or {}
    if body.get("code") == 0 and isinstance(result.get("answers"), dict):  # direct
        return result["answers"]
    raise JevError(f"unexpected response shape: {sorted(body)}")


def ask(state: dict, questions: dict, *, opener=urllib.request.urlopen, sleep=time.sleep) -> dict:
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
                return _answers(json.loads(resp.read()))
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            last = JevError(f"HTTP {e.code}: {detail}")
            if e.code not in RETRY_STATUSES:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = JevError(f"{type(e).__name__}: {e}")
        if attempt < RETRIES:
            sleep(2**attempt)
    raise last  # type: ignore[misc]
