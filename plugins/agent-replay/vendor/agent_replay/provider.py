"""Non-streaming OpenAI-compatible Chat Completions transport."""
import json
import os
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from .schema import sanitize

class ProviderError(RuntimeError):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an API credential to a redirected endpoint.
        return None

def completion(request, *, model=None, base_url=None, api_key=None, timeout=60):
    body = dict(request)
    if model:
        body["model"] = model
    if not isinstance(body.get("model"), str) or not body["model"].strip():
        raise ValueError("A provider model ID is required")
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise ValueError("Captured request must include a non-empty messages array")
    if body.get("stream"):
        raise ValueError("Streaming capture and replay are not supported in v0.1")
    body["stream"] = False
    endpoint = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    parsed = urlparse(endpoint)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and local)) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an HTTPS base URL, or an HTTP loopback URL for a local model")
    key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
    if not key and not local:
        raise ValueError("Set OPENAI_API_KEY in the runner environment before live replay")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = Request(endpoint + "/chat/completions", data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with build_opener(NoRedirect).open(req, timeout=timeout) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
            if len(raw) > 5 * 1024 * 1024:
                raise ProviderError("Provider response exceeded 5 MB")
            payload = json.loads(raw)
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        if key:
            detail = detail.replace(key, "[REDACTED]")
        raise ProviderError(f"HTTP {exc.code}: {sanitize(detail)}") from None
    except URLError as exc:
        raise ProviderError(f"Provider connection failed: {exc.reason}") from None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0].get("message"), dict):
        raise ProviderError("Provider response is missing choices[0].message")
    return payload
