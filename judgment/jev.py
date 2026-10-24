"""Minimal client for Jev's System One endpoint.

Routes, chosen by JEV_BASE_URL (default: Vercel AI Gateway):
  https://ai-gateway.vercel.sh/typesafe  key AI_GATEWAY_API_KEY or JEV_API_KEY, model typesafe-ai/jev
  https://api.typesafe.ai                key TYPESAFE_API_KEY or JEV_API_KEY, model jev-latest
  https://thejevai.com                   key JEV_API_KEY, model typesafe/jev-1.14
All take the same request; the response envelopes differ and all are accepted. JEV_MODEL overrides.
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

GATEWAY_URL = "https://ai-gateway.vercel.sh/typesafe"
TYPESAFE_URL = "https://api.typesafe.ai"
# Jev's hosts sit behind Cloudflare, which refuses urllib's default "Python-urllib" User-Agent (error 1010).
USER_AGENT = "draw-ap/1.0 (+https://github.com/CainPatel/Cal-Hacks-13.0-Stanford)"
# host -> (key env names in order of preference, default model)
ROUTES = {
    "ai-gateway.vercel.sh": (("AI_GATEWAY_API_KEY", "JEV_API_KEY"), "typesafe-ai/jev"),
    "api.typesafe.ai": (("TYPESAFE_API_KEY", "JEV_API_KEY"), "jev-latest"),
    "thejevai.com": (("JEV_API_KEY",), "typesafe/jev-1.14"),
}
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
    names, default_model = ROUTES.get(urllib.parse.urlsplit(base).hostname, (("JEV_API_KEY",), "jev-latest"))
    key = next((os.environ[n] for n in names if os.environ.get(n)), None)
    if not key:
        raise JevError(f"no Jev API key configured (looked for {' or '.join(names)} for {base})")
    return f"{base}/v1/systemone", key, os.environ.get("JEV_MODEL", default_model)


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
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": USER_AGENT},
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
