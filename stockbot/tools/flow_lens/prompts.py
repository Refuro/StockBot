"""Retail prompt archetypes sampled daily across consumer models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptArchetype:
    prompt_id: str
    text: str


# ~12 archetypes mirroring how retail users ask agentic chat apps about stocks.
RETAIL_PROMPT_ARCHETYPES: tuple[PromptArchetype, ...] = (
    PromptArchetype(
        "reddit_momentum",
        "What stocks are getting the most buzz on Reddit and social media right now? "
        "List specific tickers with a one-line reason each.",
    ),
    PromptArchetype(
        "small_cap_ideas",
        "I'm looking for small-cap stock ideas under $3B market cap with real catalysts. "
        "Give me 5 tickers and why they matter this week.",
    ),
    PromptArchetype(
        "beginner_watchlist",
        "I'm a new investor building a watchlist. What 10 tickers should I research first "
        "based on what's trending for retail traders?",
    ),
    PromptArchetype(
        "earnings_week",
        "Which companies reporting earnings this week are most interesting for a short-term "
        "swing trade? List tickers only with one sentence each.",
    ),
    PromptArchetype(
        "biotech_catalysts",
        "Any biotech or pharma names with FDA or trial news retail should know about? "
        "List tickers and the catalyst.",
    ),
    PromptArchetype(
        "meme_check",
        "What meme stocks or heavily shorted names are retail talking about today?",
    ),
    PromptArchetype(
        "sector_rotation",
        "If you had to pick 5 stocks benefiting from current sector rotation, which tickers "
        "would you name for a retail portfolio?",
    ),
    PromptArchetype(
        "breakout_scan",
        "What US stocks look like they're breaking out on volume this week? "
        "Give ticker symbols and a brief note.",
    ),
    PromptArchetype(
        "contrarian_bounce",
        "Which beaten-down stocks might be due for a bounce after a big drop? "
        "Name tickers retail might bottom-fish.",
    ),
    PromptArchetype(
        "ai_tech_wave",
        "What AI or semiconductor tickers are retail investors most excited about right now?",
    ),
    PromptArchetype(
        "insider_interest",
        "Are there any tickers where retail is suddenly interested because of insider buying "
        "or unusual options activity? List symbols.",
    ),
    PromptArchetype(
        "weekend_dd",
        "It's the weekend — what 5 tickers should I do due diligence on before Monday's open?",
    ),
)
