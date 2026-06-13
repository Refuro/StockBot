"""Dashboard glossary — single source of truth for tooltips and the /glossary page.

Plain-English copy for non-traders. Used by column headers (title=), flag chips,
panel explainers, and the standalone glossary route.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# /glossary page — short definitions, honest tone
# ---------------------------------------------------------------------------

GLOSSARY_TERMS: dict[str, str] = {
    "lane": (
        "One independent trading bot running its own virtual $100k account. "
        "The AI-gated lane and the mechanical (coin-flip) lane are twins on the same events."
    ),
    "thesis": (
        "A recorded trade idea: symbol, direction, stated probability, and the AI's written rationale. "
        "Every entry and exit links back to a thesis for audit."
    ),
    "bucket": (
        "A strategy category (e.g. earnings:ride) used to group trades for calibration. "
        "Real money only flows to buckets that pass the calibration gate."
    ),
    "ride vs fade": (
        "Ride = buy after a positive earnings surprise and hold for a rebound. "
        "Fade = bet against a crash when the AI thinks the drop is overdone."
    ),
    "mech twin": (
        "The mechanical control lane: same events, but it always acts with a fixed 55% coin-flip "
        "probability — no AI gate. If the AI lane can't beat this, the AI layer adds no value."
    ),
    "shadow lane": (
        "A lane that logs what it would have done without placing orders — used to validate "
        "behavior before any capital is deployed."
    ),
    "ensemble": (
        "Two different AI models score the same event. When they agree, we trust the answer more; "
        "when they disagree, we take the lower probability to stay conservative."
    ),
    "counter-pass / veto": (
        "A second AI pass plays devil's advocate against the primary score. "
        "If it talks the bot out of the trade, the entry is vetoed (counter_pass_veto)."
    ),
    "Brier score": (
        "Measures calibration: when the bot says 70%, does it win about 70% of the time? "
        "0 = perfect; 0.25 = no better than guessing. Lower is better."
    ),
    "calibration gate": (
        "A bucket needs at least 10 resolved predictions AND mean Brier ≤ 0.25 before it can "
        "EVER receive real money. Paper trading continues regardless."
    ),
    "time-stop / trailing / hard stop": (
        "Three automatic exit rules on open rides: hard stop sells at −8% from entry; "
        "trailing stop sells if price falls 7% from its high-water mark; "
        "time-stop closes the position after 14 days regardless."
    ),
    "slippage": (
        "The gap between the price we assumed and the price we actually got on a fill. "
        "If realized slippage is much worse than assumed, we shrink to more liquid names."
    ),
    "surprise": (
        "Earnings surprise = (actual EPS − consensus EPS) ÷ |consensus EPS|, shown as a %. "
        "+100% means the company earned double the estimate. Beware tiny estimates: a 4¢ beat on "
        "a 4¢ consensus is +100% but only 4 cents — the dashboard marks those as 'tiny est.'."
    ),
    "PIT": (
        "Point-in-time: data and decisions use only information that was knowable at that moment — "
        "no peeking at future prices or filings."
    ),
    "kill switch": (
        "An automatic halt if daily loss, drawdown, or spend limits are breached. "
        "Stops new entries until a human reviews."
    ),
    "paper vs pilot vs live": (
        "Paper = fake $100k per lane on real market data (where we are now). "
        "Pilot = up to $500 real money after shadow week + calibration pass. "
        "Live/scaled = larger capital only after the pilot proves out."
    ),
}

# ---------------------------------------------------------------------------
# Quality-gate / ensemble flags (title= on flag chips)
# ---------------------------------------------------------------------------

FLAG_TOOLTIPS: dict[str, str] = {
    "ensemble_disagreement": (
        "the two AIs differed by more than 0.15 — we kept the LOWER number to stay conservative"
    ),
    "ensemble_unavailable": (
        "the GPT cross-check was unavailable this run — graded by Claude only and capped at "
        "0.60 confidence; usually means the OpenAI account was out of funds"
    ),
    "cross_model_failed": (
        "the GPT cross-check failed this run — graded by Claude only and capped at 0.60 "
        "confidence; usually means the OpenAI account was out of funds"
    ),
    "counter_pass_veto": (
        "the devil's-advocate pass talked the primary AI out of the trade, so it was vetoed"
    ),
    "counter_pass_failed": (
        "the counter-pass could not run; trade treated as vetoed for safety"
    ),
    "counter_pass_unparseable": (
        "the counter-pass returned unusable output; trade treated as vetoed"
    ),
    "counter_pass_same_model": (
        "counter-pass ran on the same model as primary (degraded mode)"
    ),
    "no_source_doc": (
        "couldn't fetch the source doc / SEC filing that explained the move, so it wasn't scored"
    ),
    "no_filing_text": (
        "couldn't fetch readable text from the SEC filing, so it wasn't scored"
    ),
    "triaged_out": (
        "the event was filtered out before full AI scoring (low surprise or noise)"
    ),
    "hard_skip_binary_resolution": (
        "the outcome hinges on a yes/no event (trial verdict, fraud finding, going-concern or "
        "make-or-break regulatory decision) — these are skipped by code, not graded"
    ),
    "forced_flow": (
        "the selling looked forced / non-economic (index exclusion, fund liquidation, margin "
        "cascade) rather than fundamental — a setup the fade strategy looks for"
    ),
    "degraded": (
        "the AI pipeline ran in a reduced mode (a model or check was unavailable) — treat the "
        "score with extra caution"
    ),
    "strong_model_unparseable": (
        "the primary AI returned output we couldn't parse"
    ),
    "cross_model_unparseable": (
        "the GPT cross-check returned output we couldn't parse"
    ),
}

# The flags the always-visible legend covers at minimum, in display order. The
# rendered legend is the union of this list and whatever flags are actually in
# the DB, so the at-minimum set is always explained and nothing drifts.
LEGEND_FLAGS: tuple[str, ...] = (
    "ensemble_unavailable",
    "cross_model_failed",
    "ensemble_disagreement",
    "counter_pass_veto",
    "no_filing_text",
    "no_source_doc",
    "triaged_out",
    "hard_skip_binary_resolution",
    "forced_flow",
    "degraded",
)


def flag_base(flag: str) -> str:
    """Normalize a flag for lookup, tolerant of ``prefix:detail`` variants
    (e.g. ``forced_flow:index_exclusion`` → ``forced_flow``)."""
    if flag in FLAG_TOOLTIPS:
        return flag
    return flag.split(":", 1)[0]


def flag_text(flag: str) -> str:
    """Plain-English text for a flag, the single source of truth shared by the
    hover tooltips and the always-visible legend so the two never drift."""
    base = flag_base(flag)
    return FLAG_TOOLTIPS.get(base, flag)

# ---------------------------------------------------------------------------
# Table column headers (title= on <th>)
# ---------------------------------------------------------------------------

COLUMN_TOOLTIPS: dict[str, str] = {
    "symbol": "Ticker symbol of the stock",
    "qty": "Number of shares held or traded",
    "mark": "Current market price per share (from latest snapshot)",
    "pnl_usd": "Unrealized profit or loss in dollars on this open position",
    "ret_from_entry": "Percent gain or loss since the position was opened",
    "to_hard_stop": "Room left before the −8% hard stop fires (from current price)",
    "to_trail_stop": "Room left before the 7% trailing stop fires (from high-water mark)",
    "to_profit_take": "Room left before +15% profit-take (or 'taken' if half already sold)",
    "time_stop_in": "Days until the 14-day time-stop closes the position",
    "thesis": "Link to the trade thesis (rationale and audit trail)",
    "created": "When the thesis was created",
    "lane": "Which bot lane (AI-gated or mechanical twin)",
    "bucket": "Strategy bucket for calibration grouping",
    "stated_p": "Probability the bot assigned to this trade winning",
    "quality": "AI quality score (0–1) from the entry gate",
    "flags": "Warning tags from the quality / ensemble pipeline",
    "outcome": "Win, loss, or still open",
    "rationale": "The bot's written reason for the trade",
    "as_of": "Timestamp of the gate-veto decision (US Central)",
    "surprise": (
        "Earnings surprise = (actual EPS − consensus EPS) ÷ |consensus EPS|. +100% means actual "
        "was double the estimate. Tiny estimates make small beats look huge — those are marked."
    ),
    "actual_eps": "Reported earnings per share for the quarter",
    "consensus_eps": "Wall Street consensus EPS estimate going into the report",
    "triage_reason": "Why the quality gate rejected this entry",
    "filled_at": "When the broker fill was recorded",
    "side": "Buy or sell",
    "price": "Fill price per share",
    "slippage": "Realized slippage vs the assumed price",
    "resolved_trades": "Number of closed round trips with a known outcome",
    "hit_rate": "Fraction of resolved trades that were profitable",
    "mean_net_trade": "Average net return per resolved trade",
    "resolved": "When the round trip closed",
    "gated_net": "Net return on the AI-gated lane for this event",
    "mech_net": "Net return on the mechanical coin-flip lane for this event",
    "cum_gated": "Cumulative net return on the AI lane through this event",
    "cum_mech": "Cumulative net return on the mech lane through this event",
    "bot": "Bot lane identifier",
    "samples": "Number of resolved predictions in this bucket",
    "mean_brier": "Average Brier score — lower is better calibrated",
    "calibrated": "YES if bucket passes the real-money calibration gate",
    "stated_p_bin": "Range of stated probabilities in this bin",
    "n": "Count of predictions in this bin",
    "mean_stated": "Average stated probability in this bin",
    "realized_hit_rate": "Actual win rate for predictions in this bin",
    "filed": "SEC filing date",
    "company": "Company name from the filing",
    "form": "SEC form type (e.g. tender offer)",
    "matched": "Phrase that triggered the crumbs alert",
    "filing": "Link to the EDGAR filing",
    "consensus": "Flow Lens consensus score across AI models",
    "novel": "Whether this symbol is flagged as novel/unusual today",
    "models": "AI models that contributed to the consensus",
    "prompts": "Number of prompts run for this symbol",
    "mode": "Trading mode (paper, pilot, live)",
    "fills": "Number of recorded fills",
    "mean_realized": "Average realized slippage across fills",
    "max_realized": "Worst single-fill slippage",
    "assumed": "Slippage assumption baked into the simulator",
    "realized_vs_assumed": "Ratio of realized to assumed slippage",
}

# ---------------------------------------------------------------------------
# Panel explainers — collapsed <details> copy (plain HTML paragraphs)
# ---------------------------------------------------------------------------

PANEL_EXPLAINERS: dict[str, str] = {
    "ops": (
        "<p>Did today's scheduled jobs run? Green chips mean success; red means failure. "
        "The paper tick is the daily simulated trading step — it should run after the "
        "US market close on trading days.</p>"
    ),
    "positions": (
        "<p>What each lane is holding right now and how close open trades are to automatic "
        "exit rules. <strong>Hard stop</strong> sells if the position drops 8% below entry. "
        "<strong>Trailing stop</strong> sells if price falls 7% from its peak since entry. "
        "<strong>Time-stop</strong> closes after 14 days no matter what. "
        "<strong>Distance to stop</strong> = how much room is left before that rule fires.</p>"
    ),
    "theses": (
        "<p>Every trade idea the bots recorded: the AI's rationale, quality score, and any "
        "flags. Gate vetoes show trades the quality filter rejected. Recent fills link back "
        "to the thesis that caused them.</p>"
        "<p><strong>stated p</strong> = the model's probability the thesis works (0–1). "
        "<strong>quality</strong> = the 2c quality-gate score (0–1). The mechanical lane "
        "(<code>_mech</code>) shows a flat 0.55 stated p and no quality score — that's "
        "expected (it's the coin-flip control, not a bug).</p>"
        "<p><strong>surprise</strong> = (actual EPS − consensus EPS) ÷ |consensus EPS|, shown "
        "as a %. A +100% surprise means actual was double the estimate. For small-caps with a "
        "tiny consensus (a few cents), a small dollar beat balloons into a huge percent, so the "
        "raw actual-vs-consensus cents are shown and tiny estimates are marked.</p>"
    ),
    "race": (
        "<p>The whole experiment: the AI-gated lane vs an identical lane that just flips a "
        "coin (constant 55%). If the AI can't beat the coin, the AI layer is earning nothing "
        "and we'd cut it.</p>"
    ),
    "calibration": (
        "<p>When a bot says 70%, does it happen about 70% of the time? The Brier score "
        "measures that: 0 = perfect, 0.25 = no better than a coin. A bucket needs Brier "
        "≤ 0.25 over 10+ resolved predictions before it can EVER touch real money.</p>"
    ),
    "signals": (
        "<p><strong>Crumbs</strong> is a scanner for odd-lot tender offers — a niche "
        "corporate-action signal. It does not trade; &ldquo;none found&rdquo; is normal "
        "because these offers are rare.</p>"
        "<p><strong>Flow Lens</strong> is a sensor, not a trader: it asks several AI "
        "personas what tickers people are paying attention to and logs the overlap. "
        "<strong>consensus</strong> = how strongly the personas agreed; "
        "<strong>novel?</strong> = newly appearing; <strong>models</strong> = which "
        "personas; <strong>prompts</strong> = how many of the day&rsquo;s prompts "
        "mentioned it. It is accumulating attention data for the F2 study (mid-July) — "
        "there is nothing to act on yet, by design.</p>"
    ),
    "fade_decisions": (
        "<p>Each row is a crash-fade <em>decision</em>, not necessarily an open position.</p>"
        "<p><strong>skipped_fade_gate</strong> = looked at a &ge;10% crash and chose not "
        "to fade it — the common, healthy outcome. <strong>fade_buy</strong> = actually "
        "faded. <strong>fade_vetoed</strong> = the devil&rsquo;s-advocate AI killed it.</p>"
        "<p><strong>ensemble p</strong> = the two AIs&rsquo; blended probability the stock "
        "bounces within 10 days; needs &ge;0.55 to enter. <strong>&mdash;</strong> = skipped "
        "before a probability was computed (no source filing / triaged out).</p>"
        "<p><strong>outcome: open</strong> = the 10-day window hasn&rsquo;t elapsed to score "
        "it yet.</p>"
    ),
    "lanes": (
        "<p>Equity curves show each lane's fake-money account over time. Slippage compares "
        "what we assumed fills would cost vs what the simulator actually recorded — a sanity "
        "check before scaling up.</p>"
    ),
}

# Phase-journey hover titles (summary strip)
PHASE_HOVER: dict[str, str] = {
    "backtest": "Historical backtest on past data — completed",
    "paper": "Paper trading with fake $100k per lane on live market data — active now",
    "pilot": "Gate: shadow week clean + calibrated bucket → then up to $500 real money",
    "scaled": "Gate: pilot must prove out before any larger capital deployment",
}
