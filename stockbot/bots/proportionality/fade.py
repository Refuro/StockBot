"""Ticket 2.5b — the proportionality rubric + adversarial counter-pass (fade brain).

For each detected crash (2.5a), grade ONE question: was the market reaction
proportionate to the fundamental dollar change disclosed in the primary
source document? Output: proportionate / overreaction (fade candidate) /
underreaction, with a stated probability of 10-day reversion.

Pipeline (pre-registered, mirrors 2c's structure):
1. CODE-side hard-skip screen — binary-resolution situations (pending trials,
   fraud allegations, going-concern doubt, bankruptcy petitions) are skipped
   in code via regex over the document AND via the rubric models' own
   `binary_resolution` flag (either is sufficient to skip — enforcement does
   not rely on the prompt behaving). Forced-flow context (index exclusion,
   fund liquidation) is FLAGGED as the strongest setup class, never required.
2. Haiku triage — does the document substantively explain a large decline?
3. Opus 4.8 + GPT-5.5 ensemble on the rubric prompt.
   - Fade candidate only if BOTH available models classify "overreaction"
     (degraded single-model mode: the one model).
   - Disagreement rule: stated probabilities diverging by more than 0.15 cap
     the stated probability at the LOWER one + flag `ensemble_disagreement`.
   - Degradation rule: GPT unavailable -> Claude-only, cap 0.60, flag
     `ensemble_unavailable`. Never silently single-model.
4. MANDATORY adversarial counter-pass for every candidate that clears the
   entry threshold: the OTHER model (GPT, since Opus is the primary grader)
   argues the crash is CORRECT; the primary model re-reads with that argument
   and states a revised probability. Pre-registered veto rule:
       final_probability = min(ensemble_probability, revised_probability)
       vetoed iff final_probability < FADE_PROBABILITY_ENTRY_THRESHOLD.
   If GPT is down, the counter-pass runs on the primary model itself and is
   flagged `counter_pass_same_model` (mandatory means never skipped).

PRE-REGISTERED ENTRY THRESHOLD (constants below were fixed before the 2.5b
validation sample was scored; hard rule 5): a fade is entered iff the final
stated reversion probability >= FADE_PROBABILITY_ENTRY_THRESHOLD (0.55),
the classification is "overreaction", no hard skip, and no veto.

The LLM never sizes an order and never touches the risk engine — this module
returns assessments; deterministic code in ride.py does the rest.
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from stockbot.bots.proportionality.crash import CrashEvent
from stockbot.bots.proportionality.quality import fetch_event_filing_text
from stockbot.core.llm import (
    LLMRouter,
    LLMSpendCapExceeded,
    ModelTier,
    parse_json_object,
)
from stockbot.data.real_loader import CachedHTTP

logger = logging.getLogger(__name__)

PROMPT_VERSION = "fade-v1"
# Shot 11 — v2 of the adversarial counter-pass only (the rubric ensemble is
# unchanged and SHARED between v1 and v2). Pre-registered before any scoring.
PROMPT_VERSION_V2 = "fade-v2"

# --- pre-registered constants (2.5b; do not tune after seeing results) --------
FADE_PROBABILITY_ENTRY_THRESHOLD = 0.55
DISAGREEMENT_BAND = 0.15
DEGRADED_PROBABILITY_CAP = 0.60
PROBABILITY_FLOOR, PROBABILITY_CEIL = 0.05, 0.95
MAX_DOC_CHARS = 24_000

VALID_CLASSIFICATIONS = ("overreaction", "proportionate", "underreaction")

# Hard-skip screen (code-enforced; conservative, substantive phrases only —
# not boilerplate risk-factor language).
_BINARY_RESOLUTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("going_concern", re.compile(r"going[\s-]concern|substantial doubt about .{0,40}ability to continue", re.I)),
    ("fraud", re.compile(r"\bfraud(?:ulent|ulently)?\b", re.I)),
    ("trial_verdict", re.compile(r"\b(?:jury verdict|verdict|indictment|criminal charges?)\b", re.I)),
    ("bankruptcy", re.compile(r"\bchapter (?:11|7)\b|\bbankruptcy petition\b", re.I)),
)

_FORCED_FLOW_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("index_exclusion", re.compile(r"index (?:exclusion|removal|deletion)|removed from the (?:S&P|Russell|Nasdaq[- ]100)", re.I)),
    ("fund_liquidation", re.compile(r"\b(?:fund|trust) liquidat(?:ion|ing|e)\b", re.I)),
)


def binary_resolution_matches(text: str) -> list[str]:
    """Names of binary-resolution patterns present in the document (code screen)."""
    return [name for name, pat in _BINARY_RESOLUTION_PATTERNS if pat.search(text)]


def forced_flow_matches(text: str) -> list[str]:
    return [name for name, pat in _FORCED_FLOW_PATTERNS if pat.search(text)]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_TRIAGE_SYSTEM = (
    "You are a fast triage filter for a crash-analysis pipeline. The attached "
    "document is the SEC filing closest to a sharp one-day stock decline. Decide "
    "whether it substantively explains a large decline (results, guidance cut, "
    "offering/dilution, contract loss, regulatory action, clinical failure, "
    "etc.) and is worth a careful proportionality read. Routine filings that "
    "could not plausibly cause the drop are not worth scoring. Respond ONLY "
    'with JSON: {"worth_scoring": true|false, "reason": "<short>"}'
)

_RUBRIC_SYSTEM = (
    "You are an equity analyst grading ONE question about a sharp one-day stock "
    "decline: was the market reaction PROPORTIONATE to the fundamental dollar "
    "change implied by the disclosed news? Use ONLY the document text and the "
    "mechanical numbers in the header.\n"
    "Method — estimate the fundamental dollar delta with mechanical anchors "
    "where possible: guidance change x a defensible multiple, disclosed "
    "contract value, fine/judgment size, dilution amount (new shares x price), "
    "lost revenue run-rate x margin x multiple. Compare it to the market-cap "
    "change in the header.\n"
    "Classify:\n"
    '- "overreaction": the market-cap loss clearly exceeds any defensible '
    "fundamental dollar delta (fade candidate)\n"
    '- "proportionate": roughly in line (no trade)\n'
    '- "underreaction": the fundamental damage clearly exceeds the market-cap loss\n'
    "Also report:\n"
    "- binary_resolution: true if the situation resolves on a discrete future "
    "event (pending trial/verdict, fraud allegations, going-concern doubt, "
    "make-or-break regulatory decision). These are skipped by code.\n"
    "- forced_flow: true if the selling is plausibly forced/non-economic "
    "(index exclusion, fund liquidation, disclosed margin cascade).\n"
    "- probability_reversion_10d: P(the close 10 calendar days after the crash "
    "is ABOVE the crash close).\n"
    "Respond ONLY with JSON:\n"
    "{\n"
    '  "fundamental_dollar_delta_usd": <number or null>,\n'
    '  "anchors": ["<each mechanical anchor used, short>"],\n'
    '  "classification": "overreaction|proportionate|underreaction",\n'
    '  "probability_reversion_10d": <0.0-1.0>,\n'
    '  "binary_resolution": true|false,\n'
    '  "forced_flow": true|false,\n'
    '  "rationale": "<2-3 sentences>"\n'
    "}"
)

_COUNTER_SYSTEM = (
    "You are the designated devil's advocate in a trading pipeline that is "
    "about to BUY this crashed stock as an overreaction. Your job: argue the "
    "crash is CORRECT — that the decline is proportionate to (or smaller than) "
    "the true fundamental damage. Be concrete; use the document, not "
    "generalities. Respond ONLY with JSON: "
    '{"argument": "<the strongest case, <=200 words>", '
    '"strongest_point": "<one sentence>"}'
)

_REREAD_SYSTEM = (
    "You previously graded a crashed stock as an overreaction (your assessment "
    "is in the header). A counter-analyst has argued the crash is CORRECT "
    "(argument below). Re-read with fresh eyes and state your REVISED "
    "probability that the stock closes above the crash close 10 calendar days "
    "later. Changing your mind is acceptable and costless; anchoring is not. "
    'Respond ONLY with JSON: {"probability_reversion_10d": <0.0-1.0>, '
    '"rationale": "<1-2 sentences>"}'
)

# --- Shot 11: fade-v2 counter-pass (anti-sycophancy) ------------------------
# Three deliberate changes vs v1, all confined to the re-read stage:
#   1. BLIND the re-read — the model's own prior probability is never shown
#      (v1 put it in the prompt as an anchor begging to be nudged).
#   2. ENGAGEMENT BEFORE THE NUMBER — the model must state the counter-
#      argument's strongest point AND its best rebuttal in JSON before it is
#      allowed to give a probability, so it has to reason, not just agree.
#   3. RE-DERIVE, DON'T "REVISE" — phrased as "state your probability from
#      scratch", removing the small-nudge anchoring of "revise your estimate".
# The veto rule, threshold, fail-closed behaviour, and the counter-argument
# generation step are all unchanged from v1.
_COUNTER_SYSTEM_V2 = (
    "You are the designated devil's advocate in a trading pipeline that is "
    "about to BUY this crashed stock as an overreaction. Your job: argue the "
    "crash is CORRECT — that the decline is proportionate to (or smaller than) "
    "the true fundamental damage. Be concrete and use the document, not "
    "generalities; make your single most decisive point impossible to wave "
    "away. Respond ONLY with JSON: "
    '{"argument": "<the strongest case, <=200 words>", '
    '"strongest_point": "<one sentence: your single most decisive point>"}'
)

_REREAD_SYSTEM_V2 = (
    "You earlier classified this crash an overreaction. A counter-analyst has "
    "argued the crash is CORRECT (argument below). Do NOT try to be agreeable "
    "— a reflexive concession is worse than a considered disagreement. Your "
    "earlier probability is deliberately withheld; do not try to recall it. "
    "Work strictly in this order and return EVERY field:\n"
    "1. counter_strongest_point: in your own words, the SINGLE strongest point "
    "in the counter-argument — the part most likely to be right.\n"
    "2. rebuttal: your BEST rebuttal to that point, grounded in the document.\n"
    "3. probability_reversion_10d: only now, having genuinely weighed both "
    "sides, state FROM SCRATCH your probability that the stock closes ABOVE "
    "the crash close 10 calendar days later. Derive it fresh from the "
    "arguments and the document; do not 'revise' or anchor to any prior "
    "number.\n"
    'Respond ONLY with JSON: {"counter_strongest_point": "<one sentence>", '
    '"rebuttal": "<1-2 sentences>", "probability_reversion_10d": <0.0-1.0>, '
    '"rationale": "<1-2 sentences>"}'
)


def _crash_header(event: CrashEvent) -> str:
    cap = event.market_cap
    cap_change = cap * event.drop_pct if cap else None
    lines = [
        f"Company: {event.symbol}",
        (
            f"Crash: close {event.prev_close:.2f} -> {event.close:.2f} "
            f"({event.drop_pct:+.1%}) on {event.as_of.date().isoformat()}"
        ),
        (
            f"Market cap (screen-time approx): ${cap:,.0f}" if cap else "Market cap: unknown"
        ),
        (
            f"Market-cap change implied by the drop (approx): ${cap_change:,.0f}"
            if cap_change is not None
            else "Market-cap change: unknown"
        ),
    ]
    if event.source_doc is not None:
        d = event.source_doc
        lines.append(f"Source document: {d.form} accession {d.accession_number}, filed {d.filed_at}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


@dataclass
class FadeAssessment:
    event_id: str
    symbol: str
    scored: bool                         # False = no doc / triaged out / failed
    classification: str | None
    stated_probability: float | None     # FINAL (post counter-pass)
    ensemble_probability: float | None = None
    fundamental_dollar_delta_usd: float | None = None
    market_cap_change_usd: float | None = None
    hard_skip: bool = False
    hard_skip_reasons: list[str] = field(default_factory=list)
    forced_flow: bool = False
    vetoed: bool = False
    counter_pass: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    rationale: str = ""
    triage: dict[str, Any] = field(default_factory=dict)
    model_probabilities: dict[str, float] = field(default_factory=dict)
    model_classifications: dict[str, str] = field(default_factory=dict)
    prompt_version: str = PROMPT_VERSION

    @property
    def passes_entry_threshold(self) -> bool:
        return (
            self.scored
            and not self.hard_skip
            and not self.vetoed
            and self.classification == "overreaction"
            and self.stated_probability is not None
            and self.stated_probability >= FADE_PROBABILITY_ENTRY_THRESHOLD
        )


def _clamp(p: float) -> float:
    return max(PROBABILITY_FLOOR, min(PROBABILITY_CEIL, p))


def _parse_rubric(text: str) -> dict[str, Any] | None:
    data = parse_json_object(text)
    if data is None:
        return None
    try:
        prob = float(data["probability_reversion_10d"])
    except (KeyError, TypeError, ValueError):
        return None
    classification = str(data.get("classification", "")).strip().lower()
    if classification not in VALID_CLASSIFICATIONS or not (0.0 <= prob <= 1.0):
        return None
    delta = data.get("fundamental_dollar_delta_usd")
    return {
        "probability": prob,
        "classification": classification,
        "fundamental_dollar_delta_usd": float(delta) if isinstance(delta, (int, float)) else None,
        "anchors": [str(a) for a in data.get("anchors", []) if str(a).strip()],
        "binary_resolution": bool(data.get("binary_resolution", False)),
        "forced_flow": bool(data.get("forced_flow", False)),
        "rationale": str(data.get("rationale", "")).strip(),
    }


@dataclass
class _EnsembleResult:
    """The shared rubric-ensemble output (stages 1-3) for one crash.

    Shot 11: computed ONCE per crash and reused for every counter-pass version
    — only stage 4 (the counter-pass) forks by prompt version, so the marginal
    cost of a second version is just its ~2 counter-pass calls.
    """

    assessment: FadeAssessment
    header: str
    doc: str
    primary_rationale: str
    is_candidate: bool  # cleared the entry gate -> a counter-pass must run


def _run_ensemble(
    router: LLMRouter,
    *,
    event: CrashEvent,
    filing_text: str,
) -> _EnsembleResult:
    """Run stages 1-3 (code screen, Haiku triage, Opus+GPT rubric ensemble) and
    return the scored base assessment WITHOUT the counter-pass. The counter-pass
    (stage 4) is applied separately, per prompt version, on a copy of this base.
    Raises LLMSpendCapExceeded when the router's budget is gone."""
    out = FadeAssessment(
        event_id=event.event_id, symbol=event.symbol, scored=False,
        classification=None, stated_probability=None,
    )
    _no_counter = _EnsembleResult(out, "", "", "", False)
    if event.market_cap:
        out.market_cap_change_usd = event.market_cap * event.drop_pct
    header = _crash_header(event)
    doc = filing_text[:MAX_DOC_CHARS]
    if len(doc.strip()) < 200:
        out.flags.append("no_filing_text")
        out.triage = {"worth_scoring": False, "reason": "no document text available"}
        return _no_counter

    # Stage 1 (code): hard-skip screen over the document itself.
    code_matches = binary_resolution_matches(doc)
    if code_matches:
        out.hard_skip = True
        out.hard_skip_reasons = [f"code:{m}" for m in code_matches]
        out.flags.append("hard_skip_binary_resolution")
        out.rationale = f"auto-skip (code screen): {', '.join(code_matches)}"
        return _no_counter
    flow_matches = forced_flow_matches(doc)
    if flow_matches:
        out.forced_flow = True
        out.flags.extend(f"forced_flow:{m}" for m in flow_matches)

    # Stage 2: Haiku triage.
    if not router.available(ModelTier.TRIAGE):
        raise RuntimeError("triage tier unavailable — ANTHROPIC_API_KEY required for 2.5b")
    triage_resp = router.complete(
        ModelTier.TRIAGE, f"{header}\nDOCUMENT TEXT:\n{doc}", system=_TRIAGE_SYSTEM, max_tokens=200
    )
    triage = parse_json_object(triage_resp.text) or {}
    out.triage = {
        "worth_scoring": bool(triage.get("worth_scoring")),
        "reason": str(triage.get("reason", "")),
        "model_id": triage_resp.model_id,
    }
    if not out.triage["worth_scoring"]:
        out.flags.append("triaged_out")
        return _no_counter

    # Stage 3: ensemble rubric.
    if not router.available(ModelTier.STRONG):
        raise RuntimeError("strong tier unavailable — ANTHROPIC_API_KEY required for 2.5b")
    prompt = f"{header}\nDOCUMENT TEXT:\n{doc}"
    strong_resp = router.complete(ModelTier.STRONG, prompt, system=_RUBRIC_SYSTEM, max_tokens=700)
    strong = _parse_rubric(strong_resp.text)
    if strong is None:
        out.flags.append("strong_model_unparseable")
        return _no_counter
    out.model_probabilities[strong_resp.model_id] = strong["probability"]
    out.model_classifications[strong_resp.model_id] = strong["classification"]
    out.fundamental_dollar_delta_usd = strong["fundamental_dollar_delta_usd"]

    cross = None
    if router.available(ModelTier.CROSS_CHECK):
        try:
            cross_resp = router.complete(
                ModelTier.CROSS_CHECK, prompt, system=_RUBRIC_SYSTEM, max_tokens=700
            )
            cross = _parse_rubric(cross_resp.text)
            if cross is None:
                out.flags.append("cross_model_unparseable")
            else:
                out.model_probabilities[cross_resp.model_id] = cross["probability"]
                out.model_classifications[cross_resp.model_id] = cross["classification"]
        except LLMSpendCapExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade, never silently single-model
            logger.warning("cross-check model failed for %s: %s", event.symbol, exc)
            out.flags.append("cross_model_failed")

    # LLM-side hard-skip flag (either model is sufficient; enforced HERE, in code).
    llm_binary = strong["binary_resolution"] or (cross is not None and cross["binary_resolution"])
    if llm_binary:
        out.hard_skip = True
        out.hard_skip_reasons.append("llm:binary_resolution")
        out.flags.append("hard_skip_binary_resolution")
    out.forced_flow = out.forced_flow or strong["forced_flow"] or (
        cross is not None and cross["forced_flow"]
    )
    if out.forced_flow and "forced_flow" not in " ".join(out.flags):
        out.flags.append("forced_flow")

    if cross is None:
        out.flags.append("ensemble_unavailable")
        out.classification = strong["classification"]
        out.ensemble_probability = _clamp(min(strong["probability"], DEGRADED_PROBABILITY_CAP))
        out.rationale = f"[ensemble_unavailable] {strong['rationale']}"
    else:
        # Candidate rule (pre-registered): BOTH models must say overreaction.
        if strong["classification"] == cross["classification"] == "overreaction":
            out.classification = "overreaction"
        elif strong["classification"] != cross["classification"]:
            out.flags.append("classification_disagreement")
            # Conservative resolution: a fade needs unanimity; otherwise take
            # the primary model's non-fade classification (or 'proportionate'
            # when the primary said overreaction but the cross-check did not).
            out.classification = (
                strong["classification"]
                if strong["classification"] != "overreaction"
                else "proportionate"
            )
        else:
            out.classification = strong["classification"]
        if abs(strong["probability"] - cross["probability"]) > DISAGREEMENT_BAND:
            out.flags.append("ensemble_disagreement")
            out.ensemble_probability = _clamp(min(strong["probability"], cross["probability"]))
            out.rationale = (
                f"[ensemble_disagreement p={strong['probability']:.2f} vs "
                f"{cross['probability']:.2f}] {strong['rationale']}"
            )
        else:
            out.ensemble_probability = _clamp(
                (strong["probability"] + cross["probability"]) / 2.0
            )
            out.rationale = strong["rationale"] or cross["rationale"]
    out.stated_probability = out.ensemble_probability
    out.scored = True

    # Stage 4 candidate gate (pre-registered): the counter-pass runs only for
    # would-be entries — it can only veto, so sub-threshold assessments are
    # already non-entries. Identical condition for every prompt version (the
    # ensemble is shared); the counter-pass itself differs by version.
    is_candidate = (
        not out.hard_skip
        and out.classification == "overreaction"
        and out.stated_probability is not None
        and out.stated_probability >= FADE_PROBABILITY_ENTRY_THRESHOLD
    )
    return _EnsembleResult(out, header, doc, strong["rationale"], is_candidate)


def assess_crash_event(
    router: LLMRouter,
    *,
    event: CrashEvent,
    filing_text: str,
) -> FadeAssessment:
    """Run the full 2.5b pipeline (fade-v1) on one crash: the shared rubric
    ensemble plus the v1 adversarial counter-pass. Bit-identical to the
    pre-shot-11 behaviour. Raises LLMSpendCapExceeded when the budget is gone."""
    res = _run_ensemble(router, event=event, filing_text=filing_text)
    if res.is_candidate:
        _run_counter_pass(
            router, res.assessment, header=res.header, doc=res.doc,
            primary_rationale=res.primary_rationale, version=PROMPT_VERSION,
        )
    return res.assessment


def assess_crash_event_versions(
    router: LLMRouter,
    *,
    event: CrashEvent,
    filing_text: str,
    versions: tuple[str, ...] = (PROMPT_VERSION, PROMPT_VERSION_V2),
) -> dict[str, FadeAssessment]:
    """Shot 11 — run the rubric ensemble ONCE and fork the counter-pass per
    prompt version, returning one FadeAssessment per version. The triage + rubric
    calls happen a single time regardless of how many versions are requested;
    only each version's ~2 counter-pass calls are added. The fade-v1 assessment
    is bit-identical to ``assess_crash_event``."""
    res = _run_ensemble(router, event=event, filing_text=filing_text)
    out: dict[str, FadeAssessment] = {}
    for version in versions:
        # Deep-copy the shared base so each version's counter-pass mutations
        # (flags, counter_pass, stated_probability, vetoed) stay isolated.
        assessment = copy.deepcopy(res.assessment)
        assessment.prompt_version = version
        if res.is_candidate:
            _run_counter_pass(
                router, assessment, header=res.header, doc=res.doc,
                primary_rationale=res.primary_rationale, version=version,
            )
        out[version] = assessment
    return out


def _run_counter_pass(
    router: LLMRouter,
    out: FadeAssessment,
    *,
    header: str,
    doc: str,
    primary_rationale: str,
    version: str = PROMPT_VERSION,
) -> None:
    """GPT argues the crash is correct; the primary model re-reads; code applies
    the pre-registered veto rule. Mandatory — degrades to same-model, never skips.

    ``version`` selects the counter-pass prompts. fade-v1 shows the model its own
    prior probability and asks it to "revise"; fade-v2 (shot 11) blinds the prior,
    forces strongest-point + rebuttal before the number, and re-derives from
    scratch. The veto rule, threshold, and fail-closed behaviour are identical."""
    is_v2 = version == PROMPT_VERSION_V2
    counter_system = _COUNTER_SYSTEM_V2 if is_v2 else _COUNTER_SYSTEM
    reread_system = _REREAD_SYSTEM_V2 if is_v2 else _REREAD_SYSTEM
    counter_tier = (
        ModelTier.CROSS_CHECK if router.available(ModelTier.CROSS_CHECK) else ModelTier.STRONG
    )
    if counter_tier == ModelTier.STRONG:
        out.flags.append("counter_pass_same_model")
    # The task marker also appears in the user prompt so the counter-pass call
    # is distinguishable from the rubric call (and v2 from v1) in logs and stubs.
    marker = "ARGUE THE CRASH IS CORRECT (counter-pass v2)" if is_v2 else "ARGUE THE CRASH IS CORRECT"
    counter_prompt = f"{header}\nTASK: {marker}.\nDOCUMENT TEXT:\n{doc}"
    try:
        counter_resp = router.complete(
            counter_tier, counter_prompt, system=counter_system, max_tokens=400
        )
    except LLMSpendCapExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("counter-pass argument failed for %s: %s", out.symbol, exc)
        out.flags.append("counter_pass_failed")
        # Pre-registered conservative failure mode: a fade may not proceed
        # without its mandatory counter-pass.
        out.vetoed = True
        out.counter_pass = {"error": str(exc), "vetoed": True, "reason": "counter_pass_failed"}
        return
    counter = parse_json_object(counter_resp.text) or {}
    argument = str(counter.get("argument", "")).strip()
    if is_v2:
        # Shot 11 change #1: BLIND — the prior probability is deliberately
        # withheld (only the classification is named, as context for the
        # re-derivation), so there is no number to anchor to.
        reread_prompt = (
            f"{header}\n"
            f"YOUR PRIOR CLASSIFICATION: overreaction (rationale: {primary_rationale}).\n"
            f"RE-DERIVE FROM SCRATCH — your earlier probability is deliberately "
            f"withheld; do not try to recall it.\n\n"
            f"COUNTER-ARGUMENT (the crash is correct):\n{argument}\n\n"
            f"DOCUMENT TEXT:\n{doc}"
        )
    else:
        reread_prompt = (
            f"{header}\n"
            f"YOUR ORIGINAL ASSESSMENT: overreaction, probability_reversion_10d="
            f"{out.ensemble_probability:.2f}; rationale: {primary_rationale}\n\n"
            f"COUNTER-ARGUMENT (the crash is correct):\n{argument}\n\n"
            f"DOCUMENT TEXT:\n{doc}"
        )
    try:
        reread_resp = router.complete(
            ModelTier.STRONG, reread_prompt, system=reread_system, max_tokens=300
        )
    except LLMSpendCapExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("counter-pass re-read failed for %s: %s", out.symbol, exc)
        out.flags.append("counter_pass_failed")
        out.vetoed = True
        out.counter_pass = {
            "argument": argument, "error": str(exc), "vetoed": True,
            "reason": "counter_pass_failed",
        }
        return
    reread = parse_json_object(reread_resp.text) or {}
    try:
        revised = _clamp(float(reread["probability_reversion_10d"]))
    except (KeyError, TypeError, ValueError):
        out.flags.append("counter_pass_unparseable")
        out.vetoed = True
        out.counter_pass = {
            "argument": argument, "vetoed": True, "reason": "reread_unparseable",
            "counter_model_id": counter_resp.model_id,
        }
        return
    final = min(out.ensemble_probability or revised, revised)
    out.counter_pass = {
        "argument": argument,
        "strongest_point": str(counter.get("strongest_point", "")).strip(),
        "counter_model_id": counter_resp.model_id,
        "reread_model_id": reread_resp.model_id,
        "revised_probability": revised,
        "final_probability": final,
        "reread_rationale": str(reread.get("rationale", "")).strip(),
        "vetoed": final < FADE_PROBABILITY_ENTRY_THRESHOLD,
    }
    if is_v2:
        # Shot 11 change #2: the re-read had to engage before stating a number;
        # log its forced strongest-point + rebuttal for later veto-value review.
        # (Defensive: absent fields record as empty, never fail the parse.)
        out.counter_pass["reread_strongest_point"] = str(
            reread.get("counter_strongest_point", "")
        ).strip()
        out.counter_pass["reread_rebuttal"] = str(reread.get("rebuttal", "")).strip()
    out.stated_probability = final
    if final < FADE_PROBABILITY_ENTRY_THRESHOLD:
        out.vetoed = True
        out.flags.append("counter_pass_veto")


# ---------------------------------------------------------------------------
# Gate (plugged into the strategy, mirrors EarningsQualityGate)
# ---------------------------------------------------------------------------


class CrashFadeGate:
    """2.5c gate plugged into the strategy: fetch the source doc, run the pipeline.

    Caches assessments by event_id within the process so multiple lanes and
    at-least-once delivery never double-spend. Raises LLMSpendCapExceeded when
    the router's budget is gone — the strategy lets it propagate (hard rule 4).
    """

    def __init__(self, router: LLMRouter, http_client: CachedHTTP, sec_user_agent: str) -> None:
        self._router = router
        self._client = http_client
        self._sec_user_agent = sec_user_agent
        self._cache: dict[str, FadeAssessment] = {}

    @property
    def spend_summary(self) -> dict[str, Any]:
        return self._router.tracker.summary()

    def __call__(self, event: CrashEvent) -> FadeAssessment:
        if event.event_id in self._cache:
            return self._cache[event.event_id]
        if event.source_doc is None:
            out = FadeAssessment(
                event_id=event.event_id, symbol=event.symbol, scored=False,
                classification=None, stated_probability=None, flags=["no_source_doc"],
            )
            self._cache[event.event_id] = out
            return out
        try:
            text = fetch_event_filing_text(
                self._client,
                cik=event.source_doc.cik,
                accession_number=event.source_doc.accession_number,
                sec_user_agent=self._sec_user_agent,
            )
        except Exception as exc:  # noqa: BLE001 - unreachable filing != spend problem
            logger.warning(
                "source-doc fetch failed for %s (%s): %s",
                event.symbol, event.source_doc.accession_number, exc,
            )
            out = FadeAssessment(
                event_id=event.event_id, symbol=event.symbol, scored=False,
                classification=None, stated_probability=None, flags=["doc_fetch_failed"],
            )
            self._cache[event.event_id] = out
            return out
        out = assess_crash_event(self._router, event=event, filing_text=text)
        self._cache[event.event_id] = out
        return out


class SharedFadeGate:
    """Shot 11 — runs the rubric ensemble ONCE per crash and forks the
    adversarial counter-pass per prompt version (fade-v1, fade-v2).

    Exposes a per-version gate callable (``for_version``) so the v1 gated lane
    and the v2 lane share the SAME per-event ensemble cache: the expensive
    triage + Opus/GPT rubric ensemble is spent once per crash, and the only
    marginal cost of running a second version is its ~2 counter-pass calls on
    the rare candidates that clear the entry gate (hard rule 4: no double
    ensemble spend). Mirrors ``CrashFadeGate``'s fetch + fail-soft semantics;
    raises LLMSpendCapExceeded so the strategy lets it propagate.
    """

    def __init__(
        self,
        router: LLMRouter,
        http_client: CachedHTTP,
        sec_user_agent: str,
        versions: tuple[str, ...] = (PROMPT_VERSION, PROMPT_VERSION_V2),
    ) -> None:
        self._router = router
        self._client = http_client
        self._sec_user_agent = sec_user_agent
        self._versions = tuple(versions)
        self._cache: dict[str, dict[str, FadeAssessment]] = {}

    @property
    def spend_summary(self) -> dict[str, Any]:
        return self._router.tracker.summary()

    def _unscored(self, event: CrashEvent, flag: str) -> dict[str, FadeAssessment]:
        return {
            version: FadeAssessment(
                event_id=event.event_id, symbol=event.symbol, scored=False,
                classification=None, stated_probability=None, flags=[flag],
                prompt_version=version,
            )
            for version in self._versions
        }

    def assess(self, event: CrashEvent) -> dict[str, FadeAssessment]:
        """Return the {version: assessment} map for one crash, computing the
        shared ensemble once and caching by event_id."""
        if event.event_id in self._cache:
            return self._cache[event.event_id]
        if event.source_doc is None:
            result = self._unscored(event, "no_source_doc")
            self._cache[event.event_id] = result
            return result
        try:
            text = fetch_event_filing_text(
                self._client,
                cik=event.source_doc.cik,
                accession_number=event.source_doc.accession_number,
                sec_user_agent=self._sec_user_agent,
            )
        except Exception as exc:  # noqa: BLE001 - unreachable filing != spend problem
            logger.warning(
                "source-doc fetch failed for %s (%s): %s",
                event.symbol, event.source_doc.accession_number, exc,
            )
            result = self._unscored(event, "doc_fetch_failed")
            self._cache[event.event_id] = result
            return result
        result = assess_crash_event_versions(
            self._router, event=event, filing_text=text, versions=self._versions
        )
        self._cache[event.event_id] = result
        return result

    def for_version(self, version: str) -> "Callable[[CrashEvent], FadeAssessment]":
        """A FadeGate callable bound to one prompt version, sharing the cache."""
        if version not in self._versions:
            raise ValueError(f"version {version!r} not in {self._versions}")

        def gate(event: CrashEvent) -> FadeAssessment:
            return self.assess(event)[version]

        return gate
