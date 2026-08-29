"""Optional LLM pass over the messages the rules could not settle.

This is **off by default and the pipeline is complete without it**. The rules
cascade in `classify.py` resolves the large majority of a mailbox on its own, at
zero cost and with reproducible results; this module exists only to mop up the
"uncertain" tail when you want a second opinion on it.

It speaks the OpenAI chat-completions shape, so it points at any compatible
endpoint -- CompactifAI (Multiverse Computing) included -- via two environment
variables:

    SLA_LLM_BASE_URL   e.g. https://api.compactif.ai/v1
    SLA_LLM_API_KEY    the bearer token
    SLA_LLM_MODEL      e.g. cai-llama-3-1-8b-slim

The key is read from the environment only. It is never written to config, never
logged, and never committed -- `.gitignore` covers `.env`.

Messages are batched (default 40 per request) and only subject, sender and a
short preview are sent, which keeps both cost and data exposure down.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from classify import Decision
from schema import Message

DEFAULT_BATCH = 40
DEFAULT_TIMEOUT = 60

SYSTEM_PROMPT = (
    "You triage a customer success manager's inbox. For each numbered email, "
    "decide whether it needs a personal reply or action from the recipient.\n"
    "Answer YES if: someone asks them a question, requests action or approval, "
    "escalates, waits on them, or an automated mail needs a click only they can "
    "make (access grant, entity verification, organisation invitation, signature "
    "request).\n"
    "Answer NO if: newsletter, marketing, monitoring alert, calendar invite or "
    "acceptance, out-of-office, Teams missed-activity, digest, booking "
    "confirmation, FYI with no ask, or a named person other than the recipient "
    "clearly owns the next step.\n"
    'Reply with JSON only: {"results":[{"i":1,"needs_reply":true,"why":"..."}]}. '
    "Keep each 'why' under 12 words."
)


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    batch_size: int = DEFAULT_BATCH
    timeout: int = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "LLMConfig":
        base = os.environ.get("SLA_LLM_BASE_URL", "").rstrip("/")
        key = os.environ.get("SLA_LLM_API_KEY", "")
        model = os.environ.get("SLA_LLM_MODEL", "")
        missing = [
            name
            for name, value in (
                ("SLA_LLM_BASE_URL", base),
                ("SLA_LLM_API_KEY", key),
                ("SLA_LLM_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise LLMError(
                "LLM pass requested but these are unset: " + ", ".join(missing) +
                ".\nSet them in .env (see .env.example). Or drop --llm and the "
                "run stays fully offline."
            )
        return cls(base_url=base, api_key=key, model=model)


def _post(cfg: LLMConfig, payload: dict) -> dict:
    import requests

    for attempt in range(3):
        resp = requests.post(
            f"{cfg.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=cfg.timeout,
        )
        if resp.status_code == 429:
            # Throttling: back off and retry. A 403 is a permission problem and
            # is never retried.
            wait = int(resp.headers.get("Retry-After", 30)) * (attempt + 1)
            time.sleep(min(wait, 120))
            continue
        if resp.status_code == 401:
            raise LLMError(
                "401 from the LLM endpoint: the API key was rejected. Check "
                "SLA_LLM_API_KEY is the right credential for this base URL."
            )
        if resp.status_code == 403:
            raise LLMError("403 from the LLM endpoint: key lacks access. Not retrying.")
        if resp.status_code >= 400:
            raise LLMError(f"{resp.status_code} from LLM endpoint: {resp.text[:300]}")
        return resp.json()
    raise LLMError("LLM endpoint kept returning 429 after three attempts.")


def _summarise(msg: Message, index: int) -> str:
    return (
        f"{index}. from: {msg.sender.label} <{msg.sender.addr}>\n"
        f"   subject: {msg.subject}\n"
        f"   preview: {msg.body_preview[:400]}"
    )


def probe(cfg: LLMConfig | None = None) -> list[str]:
    """List available models. Cheapest way to confirm a key and base URL work."""
    import requests

    cfg = cfg or LLMConfig.from_env()
    resp = requests.get(
        f"{cfg.base_url}/models",
        headers={"Authorization": f"Bearer {cfg.api_key}"},
        timeout=cfg.timeout,
    )
    if resp.status_code >= 400:
        raise LLMError(f"{resp.status_code} from {cfg.base_url}/models: {resp.text[:300]}")
    return [m.get("id", "?") for m in resp.json().get("data", [])]


def resolve_uncertain(
    messages: list[Message],
    cfg: LLMConfig | None = None,
    verbose: bool = True,
) -> dict[str, Decision]:
    """Classify the uncertain tail. Returns uid -> Decision.

    Any message the model does not return a verdict for is simply left out, and
    the caller keeps whatever the heuristics decided.
    """
    if not messages:
        return {}
    cfg = cfg or LLMConfig.from_env()
    out: dict[str, Decision] = {}

    for start in range(0, len(messages), cfg.batch_size):
        batch = messages[start : start + cfg.batch_size]
        body = "\n\n".join(_summarise(m, i + 1) for i, m in enumerate(batch))
        payload = {
            "model": cfg.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": body},
            ],
        }
        if verbose:
            done = min(start + len(batch), len(messages))
            print(f"  LLM batch {start // cfg.batch_size + 1}: {done}/{len(messages)}")

        data = _post(cfg, payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from exc

        for item in _parse_results(content):
            idx = item.get("i")
            if not isinstance(idx, int) or not 1 <= idx <= len(batch):
                continue
            msg = batch[idx - 1]
            verdict = "needs_reply" if item.get("needs_reply") else "no_reply"
            why = str(item.get("why", ""))[:80] or "LLM triage"
            out[msg.uid] = Decision(verdict, f"LLM: {why}", 0.8)

    return out


def _parse_results(content: str) -> list[dict]:
    """Pull the results array out of a model response, tolerating code fences."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(text[start : end + 1]).get("results", [])
    except json.JSONDecodeError:
        return []
