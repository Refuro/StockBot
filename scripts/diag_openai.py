"""One-off OpenAI diagnostic for the 2c cross-check. Never prints the key."""
from __future__ import annotations

import json
import os

from openai import OpenAI

from stockbot.config import load_env

env = load_env()
key = env.openai_api_key
print(f"key present: {bool(key)}  prefix: {key[:7] + '...' if key else 'NONE'}")

client = OpenAI(api_key=key)
MODEL = "gpt-5.5"

# 1) Is the model id even valid / reachable for this account?
try:
    m = client.models.retrieve(MODEL)
    print(f"model retrieve OK: {m.id}")
except Exception as exc:  # noqa: BLE001
    print(f"model retrieve FAILED: {type(exc).__name__}: {exc}")

# 2) Reproduce the exact call the router makes, then dump the RAW response shape.
sys_prompt = (
    "You are an equity analyst. Respond ONLY with JSON: "
    '{"quality_score": <0-1>, "probability_drift_up": <0-1>, '
    '"one_offs": [], "guidance_walkback": false, "rationale": "<short>"}'
)
user_prompt = "Company: TEST. Earnings beat +10%. Strong demand, raised guidance. Score it."

try:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=700,
    )
    choice = resp.choices[0]
    content = choice.message.content
    usage = resp.usage
    print("\n--- router-style call ---")
    print(f"finish_reason: {choice.finish_reason}")
    print(f"content is None: {content is None}  len: {len(content or '')}")
    print(f"content repr: {(content or '')[:300]!r}")
    print(f"usage: prompt={usage.prompt_tokens} completion={usage.completion_tokens}")
    details = getattr(usage, "completion_tokens_details", None)
    if details:
        print(f"completion_tokens_details: {details}")
    try:
        json.loads((content or "").strip())
        print("JSON parse: OK")
    except Exception as exc:  # noqa: BLE001
        print(f"JSON parse: FAILED ({exc})")
except Exception as exc:  # noqa: BLE001
    print(f"\nrouter-style call FAILED: {type(exc).__name__}: {exc}")
