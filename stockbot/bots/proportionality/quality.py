"""Ticket 2c — LLM quality scoring for earnings surprises (the brain).

For each qualifying earnings surprise, score the DURABILITY of the beat from
the 8-K text (+ attached press-release exhibits): real demand vs one-offs
(tax items, settlements, buyback EPS effects, pulled-forward orders) and
guidance walk-backs. Output: a quality score plus a stated probability for
"this name drifts up over the hold window" — replacing the mechanical 0.55.

Pipeline (pre-registered):
1. Haiku triage — cheap read; kills filings with no scoreable earnings content.
2. Opus 4.8 + GPT-5.5 ensemble for survivors.
   - Disagreement rule: if the two stated probabilities diverge by more than
     0.15, cap the stated probability at the LOWER one and flag
     `ensemble_disagreement` in the thesis rationale.
   - Degradation rule: if OpenAI is unavailable at runtime, run Claude-only,
     flag `ensemble_unavailable`, and cap stated probability at 0.60.
     Never silently single-model.

PRE-REGISTERED ENTRY THRESHOLD (written before the validation sample was
scored; see hard rule 4): a 2b mechanical entry is taken iff the 2c stated
probability >= QUALITY_PROBABILITY_ENTRY_THRESHOLD (0.55). One number, one
field, no post-hoc tuning.

The LLM never sizes an order and never touches the risk engine — this module
returns scores that feed thesis records; deterministic code does the rest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from stockbot.bots.frontier.filings import html_to_text
from stockbot.core.llm import (
    LLMRouter,
    LLMSpendCapExceeded,
    ModelTier,
    parse_json_object,
)
from stockbot.data.real_loader import CachedHTTP

logger = logging.getLogger(__name__)

PROMPT_VERSION = "qscore-v1"

# Pre-registered BEFORE the validation study ran (hard rule 4).
QUALITY_PROBABILITY_ENTRY_THRESHOLD = 0.55
DISAGREEMENT_BAND = 0.15
DEGRADED_PROBABILITY_CAP = 0.60
PROBABILITY_FLOOR, PROBABILITY_CEIL = 0.05, 0.95

# Document budget per event: keeps Opus input ~6-8k tokens.
MAX_DOC_CHARS = 24_000

ARCHIVES_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/index.json"
ARCHIVES_FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{name}"


# ---------------------------------------------------------------------------
# 8-K text retrieval (cached, free)
# ---------------------------------------------------------------------------


def fetch_event_filing_text(
    client: CachedHTTP, *, cik: int, accession_number: str, sec_user_agent: str
) -> str:
    """Plain text of the 8-K primary document + press-release exhibits (EX-99)."""
    acc_nodash = accession_number.replace("-", "")
    index = client.get_json(
        ARCHIVES_INDEX_URL.format(cik=cik, acc_nodash=acc_nodash),
        headers={"User-Agent": sec_user_agent},
    )
    items = index.get("directory", {}).get("item", [])
    names = [str(i.get("name", "")) for i in items]
    docs = [
        n for n in names
        if n.lower().endswith((".htm", ".html"))
        and "index" not in n.lower()
        and not n.lower().startswith("r")  # XBRL viewer renders (R1.htm ...)
    ]
    # Primary 8-K first, then exhibits (press releases are usually ex99*).
    docs.sort(key=lambda n: (0 if "8k" in n.lower() or "8-k" in n.lower() else 1, n.lower()))
    chunks: list[str] = []
    total = 0
    for name in docs:
        if total >= MAX_DOC_CHARS:
            break
        try:
            html = client.get_text(
                ARCHIVES_FILE_URL.format(cik=cik, acc_nodash=acc_nodash, name=name),
                headers={"User-Agent": sec_user_agent},
            )
        except Exception as exc:  # noqa: BLE001 - skip unfetchable exhibits
            logger.warning("could not fetch %s for %s: %s", name, accession_number, exc)
            continue
        text = html_to_text(html)
        if len(text) < 200:
            continue
        take = text[: MAX_DOC_CHARS - total]
        chunks.append(f"--- DOCUMENT: {name} ---\n{take}")
        total += len(take)
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_TRIAGE_SYSTEM = (
    "You are a fast triage filter for an earnings-event pipeline. Decide whether the "
    "filing text contains enough substantive earnings information (results, drivers, "
    "guidance) to be worth a careful read. Respond ONLY with JSON: "
    '{"worth_scoring": true|false, "reason": "<short>"}'
)

_SCORE_SYSTEM = (
    "You are an equity analyst assessing ONE question: how durable is this earnings beat? "
    "Real demand is durable; one-offs are not (tax items, legal settlements, asset sales, "
    "buyback-driven EPS, pulled-forward orders, accounting changes). Guidance walk-backs "
    "or lowered outlooks undermine durability. Use ONLY the provided filing text.\n"
    "Respond ONLY with JSON:\n"
    "{\n"
    '  "quality_score": <0.0-1.0, durability of the beat>,\n'
    '  "probability_drift_up": <0.0-1.0, P(stock drifts up over the next ~2 weeks)>,\n'
    '  "one_offs": ["<each one-off item you found, short>"],\n'
    '  "guidance_walkback": true|false,\n'
    '  "rationale": "<2-3 sentences>"\n'
    "}"
)


def _event_header(symbol: str, payload: dict[str, Any]) -> str:
    return (
        f"Company: {symbol}\n"
        f"Earnings surprise: actual EPS {payload.get('actual_eps')} vs consensus "
        f"{payload.get('consensus_eps')} (surprise {payload.get('surprise_pct')})\n"
        f"Filed: {payload.get('filing_date')} (8-K, accession {payload.get('accession_number')})\n"
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class QualityAssessment:
    event_id: str
    symbol: str
    scored: bool                       # False = triaged out or scoring failed
    quality_score: float | None
    stated_probability: float | None
    flags: list[str] = field(default_factory=list)
    rationale: str = ""
    one_offs: list[str] = field(default_factory=list)
    guidance_walkback: bool = False
    triage: dict[str, Any] = field(default_factory=dict)
    model_probabilities: dict[str, float] = field(default_factory=dict)
    model_quality: dict[str, float] = field(default_factory=dict)
    prompt_version: str = PROMPT_VERSION

    @property
    def passes_entry_threshold(self) -> bool:
        return (
            self.scored
            and self.stated_probability is not None
            and self.stated_probability >= QUALITY_PROBABILITY_ENTRY_THRESHOLD
        )


def _clamp(p: float) -> float:
    return max(PROBABILITY_FLOOR, min(PROBABILITY_CEIL, p))


def _parse_score(text: str) -> tuple[float, float, list[str], bool, str] | None:
    data = parse_json_object(text)
    if data is None:
        return None
    try:
        quality = float(data["quality_score"])
        prob = float(data["probability_drift_up"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= quality <= 1.0 and 0.0 <= prob <= 1.0):
        return None
    one_offs = [str(x) for x in data.get("one_offs", []) if str(x).strip()]
    walkback = bool(data.get("guidance_walkback", False))
    rationale = str(data.get("rationale", "")).strip()
    return quality, prob, one_offs, walkback, rationale


class EarningsQualityGate:
    """2c gate plugged into the ride strategy: fetch the 8-K, run the pipeline.

    Caches assessments by event_id within the process so at-least-once event
    delivery never double-spends. Raises LLMSpendCapExceeded when the router's
    budget is gone — the strategy lets it propagate (hard rule 6).
    """

    def __init__(self, router: LLMRouter, http_client: CachedHTTP, sec_user_agent: str) -> None:
        self._router = router
        self._client = http_client
        self._sec_user_agent = sec_user_agent
        self._cache: dict[str, QualityAssessment] = {}

    @property
    def spend_summary(self) -> dict[str, Any]:
        return self._router.tracker.summary()

    def __call__(self, surprise_event: Any) -> QualityAssessment:
        event_id = surprise_event.event_id
        if event_id in self._cache:
            return self._cache[event_id]
        payload = dict(surprise_event.payload)
        payload.setdefault("surprise_pct", surprise_event.surprise_pct)
        payload.setdefault("actual_eps", surprise_event.actual_eps)
        payload.setdefault("consensus_eps", surprise_event.consensus_eps)
        cik = payload.get("cik")
        accession = payload.get("accession_number")
        if not cik or not accession:
            out = QualityAssessment(
                event_id=event_id, symbol=surprise_event.symbol, scored=False,
                quality_score=None, stated_probability=None, flags=["no_filing_ref"],
            )
            self._cache[event_id] = out
            return out
        try:
            text = fetch_event_filing_text(
                self._client, cik=int(cik), accession_number=str(accession),
                sec_user_agent=self._sec_user_agent,
            )
        except Exception as exc:  # noqa: BLE001 - unreachable filing != spend problem
            logger.warning("8-K fetch failed for %s (%s): %s", surprise_event.symbol, accession, exc)
            out = QualityAssessment(
                event_id=event_id, symbol=surprise_event.symbol, scored=False,
                quality_score=None, stated_probability=None, flags=["filing_fetch_failed"],
            )
            self._cache[event_id] = out
            return out
        out = score_earnings_event(
            self._router, event_id=event_id, symbol=surprise_event.symbol,
            payload=payload, filing_text=text,
        )
        self._cache[event_id] = out
        return out


def score_earnings_event(
    router: LLMRouter,
    *,
    event_id: str,
    symbol: str,
    payload: dict[str, Any],
    filing_text: str,
) -> QualityAssessment:
    """Run triage -> ensemble on one earnings event. Raises LLMSpendCapExceeded
    when the router's budget is gone (caller decides how to stop)."""
    out = QualityAssessment(
        event_id=event_id, symbol=symbol, scored=False,
        quality_score=None, stated_probability=None,
    )
    header = _event_header(symbol, payload)
    doc = filing_text[:MAX_DOC_CHARS]
    if len(doc.strip()) < 200:
        out.flags.append("no_filing_text")
        out.triage = {"worth_scoring": False, "reason": "no filing text available"}
        return out

    # Stage 1: Haiku triage.
    if not router.available(ModelTier.TRIAGE):
        raise RuntimeError("triage tier unavailable — ANTHROPIC_API_KEY required for 2c")
    triage_resp = router.complete(
        ModelTier.TRIAGE, f"{header}\nFILING TEXT:\n{doc}", system=_TRIAGE_SYSTEM, max_tokens=200
    )
    triage = parse_json_object(triage_resp.text) or {}
    out.triage = {
        "worth_scoring": bool(triage.get("worth_scoring")),
        "reason": str(triage.get("reason", "")),
        "model_id": triage_resp.model_id,
    }
    if not out.triage["worth_scoring"]:
        out.flags.append("triaged_out")
        return out

    # Stage 2: ensemble.
    if not router.available(ModelTier.STRONG):
        raise RuntimeError("strong tier unavailable — ANTHROPIC_API_KEY required for 2c")
    prompt = f"{header}\nFILING TEXT:\n{doc}"
    strong_resp = router.complete(ModelTier.STRONG, prompt, system=_SCORE_SYSTEM, max_tokens=700)
    strong = _parse_score(strong_resp.text)
    if strong is None:
        out.flags.append("strong_model_unparseable")
        return out
    s_quality, s_prob, s_one_offs, s_walkback, s_rationale = strong
    out.model_quality[strong_resp.model_id] = s_quality
    out.model_probabilities[strong_resp.model_id] = s_prob
    out.one_offs = s_one_offs
    out.guidance_walkback = s_walkback

    cross = None
    if router.available(ModelTier.CROSS_CHECK):
        try:
            cross_resp = router.complete(
                ModelTier.CROSS_CHECK, prompt, system=_SCORE_SYSTEM, max_tokens=700
            )
            cross = _parse_score(cross_resp.text)
            if cross is None:
                out.flags.append("cross_model_unparseable")
            else:
                out.model_quality[cross_resp.model_id] = cross[0]
                out.model_probabilities[cross_resp.model_id] = cross[1]
        except LLMSpendCapExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade, never silently single-model
            logger.warning("cross-check model failed for %s: %s", symbol, exc)
            out.flags.append("cross_model_failed")

    if cross is None:
        # Pre-registered degradation rule: Claude-only, capped, flagged.
        out.flags.append("ensemble_unavailable")
        out.quality_score = s_quality
        out.stated_probability = _clamp(min(s_prob, DEGRADED_PROBABILITY_CAP))
        out.rationale = f"[ensemble_unavailable] {s_rationale}"
    else:
        c_quality, c_prob, _, c_walkback, c_rationale = cross
        out.guidance_walkback = s_walkback or c_walkback
        out.quality_score = (s_quality + c_quality) / 2.0
        if abs(s_prob - c_prob) > DISAGREEMENT_BAND:
            out.flags.append("ensemble_disagreement")
            out.stated_probability = _clamp(min(s_prob, c_prob))
            out.rationale = (
                f"[ensemble_disagreement p={s_prob:.2f} vs {c_prob:.2f}] {s_rationale}"
            )
        else:
            out.stated_probability = _clamp((s_prob + c_prob) / 2.0)
            out.rationale = s_rationale or c_rationale
    out.scored = True
    return out
