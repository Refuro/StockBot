"""Code-side extraction guards (ticket 3a-F) from the 3b grading failure taxonomy.

The 3b checkpoint failed at 54% (`reports/3b-grading.md`); the failures
clustered into five mechanical patterns. Each guard below rejects one pattern,
deterministically, from the quoted passage itself — no LLM involved. The LLM's
answers were already re-validated against the passage (shot 2); these guards
re-validate the *semantics* the grader found wrong.

Guards:
1. revenue-basis  — a concentration % whose nearest basis is accounts
   receivable (not revenue/sales) is rejected.
2. aggregate-group — a % attached to a plural customer group ("our four
   largest customers accounted for 71%") is rejected unless the percentages
   are individually distributed over the named customers.
3. operative-decree — regulatory edges require filer-as-party operative
   language; proposed / under-discussion / satisfied / terminated decrees and
   third-party decrees are rejected.
4. supplier-direction — "products from X ... % of net sales" is a supplier
   relationship, not customer concentration.
5. anonymous-placeholder — "Customer C" style placeholders are not named
   entities; they are demoted to the anonymous pool.
6. counterparty-category (ticket 3a-G, from the 3b re-grade) — plural
   program/payor groups ("government healthcare programs, including Medicare
   and Medicaid") are categories, not tradeable counterparties; demoted to
   the anonymous pool like placeholders.

Plus `canonical_counterparty`: conservative entity normalization for dedupe
(EPA == Environmental Protection Agency; Caremark LLC == CVS Caremark).

Round 3 (ticket 3a-H, from the seed-3003 grading `reports/3b-grading-3.md`,
32/50 = 64% FAIL): seven guards G1-G7 at the bottom of this module, applied to
WHOLE EDGES (type + counterparty + magnitude + quote) rather than raw passages.
Round 4 (shot 7 Agent A, from the seed-3004 grading `reports/3b-grading-4.md`,
44/50 = 88% FAIL): extends G1, G2, G6, G7 and adds G8 (filer-product as
counterparty) and G9 (hypothetical regulatory boilerplate). All patterns were
pre-registered against the 50 graded fixture edges BEFORE the graph re-filter
ran (hard rule in `handoff-shot6.md`); the fixture acceptance test is
`tests/test_shot6_3aH_guards.py` and the fixtures are
frozen. `apply_edge_guard_stack` composes rounds 1-3 for the re-filter.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from stockbot.bots.frontier.resolve import normalize_company_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guard 1 — revenue basis
# ---------------------------------------------------------------------------

_REVENUE_BASIS_RE = re.compile(
    r"\b(revenues?|net sales|total sales|gross sales|net product (sales|revenues?)"
    r"|gross product revenues?|sales)\b",
    re.IGNORECASE,
)
_RECEIVABLE_BASIS_RE = re.compile(
    r"\b((gross |net |total )?accounts receivable|trade receivables?|receivables?)\b",
    re.IGNORECASE,
)

_BASIS_WINDOW = 200  # chars around a % occurrence in which a basis word must sit


def _pct_occurrences(passage: str, pct: float) -> list[int]:
    """Start offsets of every literal occurrence of the percentage token."""
    forms = {f"{pct:g}", f"{pct:.1f}", f"{pct:.0f}"}
    spots: list[int] = []
    for form in forms:
        for m in re.finditer(rf"\b{re.escape(form)}\s?%", passage):
            spots.append(m.start())
    return sorted(set(spots))


def _nearest_distance(passage: str, pos: int, pattern: re.Pattern) -> int | None:
    best: int | None = None
    for m in pattern.finditer(passage):
        mid = (m.start() + m.end()) // 2
        dist = abs(mid - pos)
        if dist <= _BASIS_WINDOW and (best is None or dist < best):
            best = dist
    return best


def revenue_basis_certified(passage: str, pct: float) -> bool:
    """True iff at least one occurrence of the % has revenue/sales as its
    NEAREST basis word (closer than any receivables basis)."""
    for pos in _pct_occurrences(passage, pct):
        rev = _nearest_distance(passage, pos, _REVENUE_BASIS_RE)
        rec = _nearest_distance(passage, pos, _RECEIVABLE_BASIS_RE)
        if rev is not None and (rec is None or rev < rec):
            return True
    return False


# ---------------------------------------------------------------------------
# Guard 2 — aggregate group
# ---------------------------------------------------------------------------

_NUM_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_GROUP_RE = re.compile(
    r"\b(?:top\s+)?(two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:largest\s+|significant\s+|major\s+)?"
    r"(?:customers|clients|wholesalers|distributors)\b",
    re.IGNORECASE,
)
_AGG_PHRASE_RE = re.compile(
    r"\b(?:customers|clients)[^.;]{0,40}\b(?:collectively|in the aggregate)\b", re.IGNORECASE
)
_PCT_LIST_RE = re.compile(r"\d{1,2}(?:\.\d+)?\s?%")


# Abbreviations whose trailing period is NOT a sentence boundary.
_ABBREVS = ("Inc", "Co", "Corp", "Ltd", "LLC", "L.P", "Mr", "Dr", "No", "St", "U.S", "Jr")


def _clause_before(passage: str, pos: int) -> str:
    """Text from the last sentence/clause boundary up to the % occurrence,
    ignoring periods that belong to company suffixes ("Inc. and ...")."""
    boundary = -1
    for m in re.finditer(r"[.;](?=\s)", passage[:pos]):
        if passage[m.start()] == "." and passage[:m.start()].rstrip().endswith(_ABBREVS):
            continue
        boundary = m.start()
    return passage[boundary + 1 : pos] if boundary >= 0 else passage[:pos]


def _group_size(clause: str) -> int | None:
    m = None
    for m in _GROUP_RE.finditer(clause):
        pass  # keep the LAST group mention before the %
    if m is None:
        return None
    token = m.group(1).lower()
    return _NUM_WORDS.get(token, int(token) if token.isdigit() else None)


def aggregate_group_pct(passage: str, pct: float) -> bool:
    """True if every occurrence of the % attaches to a plural customer group.

    Exception: when the clause lists exactly as many percentages as the group
    has members ("three wholesalers ... accounted for 31%, 26% and 22% ...
    respectively"), the figures are individually attributed — not an aggregate.
    """
    occurrences = _pct_occurrences(passage, pct)
    if not occurrences:
        return False
    verdicts: list[bool] = []
    for pos in occurrences:
        clause = _clause_before(passage, pos)
        if _AGG_PHRASE_RE.search(clause):
            verdicts.append(True)
            continue
        size = _group_size(clause)
        if size is None:
            verdicts.append(False)
            continue
        # Count the consecutive %-list this occurrence belongs to.
        tail = passage[pos : pos + 160]
        head_pcts = _PCT_LIST_RE.findall(clause[-60:])
        list_len = len(head_pcts) + len(_PCT_LIST_RE.findall(tail.split("of", 1)[0]))
        verdicts.append(list_len != size)
    return all(verdicts)


# ---------------------------------------------------------------------------
# Guard 3 — operative decree
# ---------------------------------------------------------------------------

_DECREE_TERM_RE = re.compile(
    r"consent\s+decree|injunction|corporate\s+integrity\s+agreement"
    r"|deferred\s+prosecution\s+agreement|settlement",
    re.IGNORECASE,
)
_DECREE_REJECT_RE = re.compile(
    r"\b(proposed|discussing|negotiat\w+|satisfied|terminated|expired"
    r"|would\s+be\s+established|contemplated)\b",
    re.IGNORECASE,
)
_DECREE_OPERATIVE_RE = re.compile(
    r"\b(entered\s+into|operat\w+\s+under|under\s+(a|the)|subject\s+to"
    r"|must\s+comply|comply\s+(with|at\s+all\s+times)|in\s+accordance\s+with"
    r"|agreed\s+to|required\s+to\s+comply)\b"
    r".{0,90}?"
    r"(consent\s+decree|injunction|corporate\s+integrity|deferred\s+prosecution|settlement)",
    re.IGNORECASE | re.DOTALL,
)
_REGULATOR_SUBJECT_RE = re.compile(
    r"\b(EPA|SEC|DOJ|FDA|FTC|the\s+agency)\s+(has\s+)?(published|proposed|filed|issued)\b",
    re.IGNORECASE,
)

_DECREE_CONTEXT = 160


def operative_decree(passage: str) -> bool:
    """True iff the passage asserts an OPERATIVE decree binding the filer."""
    for term in _DECREE_TERM_RE.finditer(passage):
        lo = max(0, term.start() - _DECREE_CONTEXT)
        hi = min(len(passage), term.end() + _DECREE_CONTEXT)
        context = passage[lo:hi]
        if _DECREE_REJECT_RE.search(context):
            continue
        if _REGULATOR_SUBJECT_RE.search(context):
            continue
        if _DECREE_OPERATIVE_RE.search(context):
            return True
    return False


# ---------------------------------------------------------------------------
# Guard 4 — supplier direction
# ---------------------------------------------------------------------------


def supplier_direction(passage: str, counterparty_name: str) -> bool:
    """True if the % actually measures purchases/products FROM the counterparty
    (a supplier relationship masquerading as customer concentration)."""
    # Match on the first 1-2 name tokens so "Cisco Systems" catches
    # "products from Cisco Systems, which represented...".
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", counterparty_name) if t]
    if not tokens:
        return False
    name_head = r"\s+".join(re.escape(t) for t in tokens[:2])
    pattern = re.compile(
        rf"\b(?:products?|purchases?|merchandise|goods|inventory)\s+"
        rf"(?:purchased\s+|sourced\s+)?from\s+(?:[A-Za-z.,'\s]{{0,30}})?{name_head}"
        rf"|\bsales\s+of\s+{name_head}(?:'s)?\s+products?",
        re.IGNORECASE,
    )
    return bool(pattern.search(passage))


# ---------------------------------------------------------------------------
# Guard 5 — anonymous placeholder
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(
    r"^(?:customer|client|distributor|supplier)\s+"
    r"(?:[A-Z]|\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)$",
    re.IGNORECASE,
)


def is_anonymous_placeholder(name: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(name.strip()))


# ---------------------------------------------------------------------------
# Guard 6 — counterparty category (ticket 3a-G)
# ---------------------------------------------------------------------------

# Plural head nouns that make a counterparty a CATEGORY of payors/customers
# rather than a single entity ("government healthcare programs", "commercial
# insurers"). Singular forms ("Kaiser Foundation Health Plan") never match.
_CATEGORY_HEAD_RE = re.compile(
    r"\b(programs|payors|payers|agencies|plans|insurers|providers|pharmacies"
    r"|hospitals|customers|clients|distributors|wholesalers|retailers"
    r"|entities|organizations|institutions|intermediaries)\s*$",
    re.IGNORECASE,
)

# Government reimbursement programs: a conjunction of these ("Medicare and
# Medicaid") is a collective, not an entity. Individually they are still
# rejected as counterparties — a program is not a tradeable entity either,
# but that judgment is left to resolution; this guard only handles groups.
_GOVT_PROGRAMS = {"medicare", "medicaid", "tricare", "chip", "medi-cal"}


def is_counterparty_category(name: str) -> bool:
    """True if the name denotes a plural payor/program/customer CATEGORY
    ("government healthcare programs, including Medicare and Medicaid")
    rather than a single named entity."""
    base = name.strip().rstrip(".")
    # Strip an exemplifying tail so the head noun is what gets judged.
    base = re.split(r",?\s+(?:including|such as)\b", base, flags=re.IGNORECASE)[0]
    base = base.strip().rstrip(",")
    # Plural head noun + generic (no proper-noun token) = category. The case
    # check keeps proper names like "Kaiser Foundation Hospitals" (graded
    # correct in the 3b re-grade) out of the guard's reach: counterparty
    # names are quoted exactly as written in the filing, so a genuinely
    # generic group reads all-lowercase mid-sentence.
    has_proper_token = any(t[:1].isupper() for t in base.split())
    if _CATEGORY_HEAD_RE.search(base) and not has_proper_token:
        return True
    parts = [
        p.strip().lower()
        for p in re.split(r"\s*(?:,|\band\b|&|/)\s*", base, flags=re.IGNORECASE)
        if p.strip()
    ]
    return len(parts) >= 2 and all(p in _GOVT_PROGRAMS for p in parts)


# ---------------------------------------------------------------------------
# Entity-normalized dedupe
# ---------------------------------------------------------------------------

# Conservative alias map (normalized form -> canonical form). Extend only with
# unambiguous identities; never fuzzy.
_CANONICAL_ALIASES = {
    "EPA": "ENVIRONMENTAL PROTECTION AGENCY",
    "U S EPA": "ENVIRONMENTAL PROTECTION AGENCY",
    "US EPA": "ENVIRONMENTAL PROTECTION AGENCY",
    "U S ENVIRONMENTAL PROTECTION AGENCY": "ENVIRONMENTAL PROTECTION AGENCY",
    "US ENVIRONMENTAL PROTECTION AGENCY": "ENVIRONMENTAL PROTECTION AGENCY",
    "SEC": "SECURITIES AND EXCHANGE COMMISSION",
    "U S SECURITIES AND EXCHANGE COMMISSION": "SECURITIES AND EXCHANGE COMMISSION",
    "DOJ": "DEPARTMENT OF JUSTICE",
    "U S DEPARTMENT OF JUSTICE": "DEPARTMENT OF JUSTICE",
    "US DEPARTMENT OF JUSTICE": "DEPARTMENT OF JUSTICE",
    "FDA": "FOOD AND DRUG ADMINISTRATION",
    "U S FOOD AND DRUG ADMINISTRATION": "FOOD AND DRUG ADMINISTRATION",
    "FTC": "FEDERAL TRADE COMMISSION",
    "OIG": "OFFICE OF INSPECTOR GENERAL HHS",
    "OIG HHS": "OFFICE OF INSPECTOR GENERAL HHS",
    "OFFICE OF INSPECTOR GENERAL OF THE U S DEPARTMENT OF HEALTH AND HUMAN SERVICES": (
        "OFFICE OF INSPECTOR GENERAL HHS"
    ),
    "CAREMARK": "CVS CAREMARK",
}


def canonical_counterparty(name: str) -> str:
    """Normalized + alias-mapped identity used for edge dedupe."""
    normalized = normalize_company_name(name)
    return _CANONICAL_ALIASES.get(normalized, normalized)


# ===========================================================================
# Round 3 (ticket 3a-H) — edge-level guards from the seed-3003 grading.
#
# Evaluation order is pre-registered: G1, G2, G9 (regulatory only), G4, G8
# (sole_supplier only), G7, G5, G6 are KILL guards applied in that order
# (first hit wins attribution); G3 is a DEMOTION
# (named -> anonymous) evaluated independently — a demoted edge can still be
# killed. G4 and G7 only apply to relationship edges (customer_concentration /
# sole_supplier): regulatory_precedent edges legitimately reference resolved,
# historical enforcement (3b-grading-3 graded GPOR/USEPA correct), so
# termination language must NOT veto them.
# ===========================================================================

from stockbot.bots.frontier.candidates import (  # noqa: E402  (placed with round 3)
    CUSTOMER_CONCENTRATION,
    REGULATORY_PRECEDENT,
    SOLE_SUPPLIER,
)

# --- shared sentence splitting (abbreviation-aware) ------------------------

_SENT_SPLIT_RE = re.compile(r"[.;](?=\s)")


def _sentences(passage: str) -> list[str]:
    """Split on sentence boundaries, ignoring periods in company suffixes."""
    out: list[str] = []
    start = 0
    for m in _SENT_SPLIT_RE.finditer(passage):
        if passage[m.start()] == "." and passage[: m.start()].rstrip().endswith(_ABBREVS):
            continue
        out.append(passage[start : m.start() + 1])
        start = m.end()
    out.append(passage[start:])
    return [s.strip() for s in out if s.strip()]


def _head_token(name: str) -> str:
    """First substantial token of a counterparty name, for sentence scoping."""
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", name) if t]
    return tokens[0] if tokens else name


def _norm_words(name: str) -> str:
    """Lowercase, punctuation collapsed to single spaces ("U.S." -> "u s")."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


# ---------------------------------------------------------------------------
# G1 — internal-entity counterparty (taxonomy #4 BMD segment, #25 Middlesex
# System, #27 APT plant, #36 client vertical). The counterparty must be
# EXTERNAL; a filer's own segment/plant/system/division/vertical is not one.
# ---------------------------------------------------------------------------

# Lowercase organizational-unit head nouns. The head token must be lowercase
# in the original name ("APT plant", "BMD segment") — proper names like
# "Cisco Systems" keep their capitalized head and are NOT internal entities.
_INTERNAL_UNIT_HEADS = {
    "segment", "segments", "division", "divisions", "plant", "plants",
    "facility", "facilities", "system", "systems", "vertical", "verticals",
    "unit", "units",
}

# "<name> produced/generates ... of our ... revenues" — an entity that
# PRODUCES the filer's revenue is internal infrastructure, not a customer
# (customers "account for" / "represent" revenue). Catches "The Middlesex
# System produced approximately 67% of our 2025 consolidated operating
# revenues" where the head noun is capitalized.
_INTERNAL_PRODUCES_TMPL = (
    r"{name}\s+(?:also\s+)?(?:produced|produces|generated|generates)\b"
    r"[^.;]{{0,90}}?\bof\s+our\b"
)
# Apostrophe variants in filings (ASCII and curly quotes).
_APOS_S = "'\u2019\u2018"
_SELF_SEGMENT_REVENUE_TMPL = (
    "{name}[{apos}]s\\s+revenue\\s+(?:represented|accounted\\s+for)\\s+"
    "[^;]{{0,80}}?\\bof\\s+our\\s+(?:total\\s+)?(?:consolidated\\s+)?"
    "(?:revenue|revenues|net\\s+sales)\\b"
    "|{name}\\s+revenue\\s+(?:represented|accounted\\s+for)\\s+"
    "[^;]{{0,80}}?\\bof\\s+our\\s+(?:total\\s+)?(?:consolidated\\s+)?"
    "(?:revenue|revenues|net\\s+sales)\\b"
)


def is_internal_entity(counterparty_name: str, passage: str) -> bool:
    name = counterparty_name.strip()
    tokens = name.split()
    if tokens:
        head = tokens[-1]
        # Lowercase unit suffix only ("segment", "plant") — not "Systems" in
        # proper names like Cisco Systems. Possessive "our X" alone is NOT G1
        # ("Our Initial Customer" is an external nickname, edge #7 → G5).
        if head == head.lower() and head.lower() in _INTERNAL_UNIT_HEADS:
            return True
    esc = re.escape(name)
    if re.search(_INTERNAL_PRODUCES_TMPL.format(name=esc), passage, re.IGNORECASE):
        return True
    if re.search(
        _SELF_SEGMENT_REVENUE_TMPL.format(name=esc, apos=_APOS_S), passage, re.IGNORECASE
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# G2 — non-entity counterparty (taxonomy #15 Consent Decree, #26 Italy,
# #30 "U.S.", #46 "federal and state governments").
# ---------------------------------------------------------------------------

# Geographies seen in revenue-by-region tables. Exact full-name match only —
# "U.S. Army" must never match via its "U.S." prefix.
_GEOGRAPHY_NAMES = {
    _norm_words(n)
    for n in (
        "Italy", "France", "Germany", "Spain", "Portugal", "Netherlands",
        "Belgium", "Switzerland", "Austria", "Poland", "Sweden", "Norway",
        "Denmark", "Finland", "Ireland", "United Kingdom", "UK", "Greece",
        "Russia", "Turkey", "China", "Japan", "India", "South Korea", "Korea",
        "Taiwan", "Singapore", "Hong Kong", "Vietnam", "Thailand", "Malaysia",
        "Indonesia", "Philippines", "Australia", "New Zealand", "Canada",
        "Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Peru",
        "Israel", "Saudi Arabia", "United Arab Emirates", "UAE", "Egypt",
        "South Africa", "Nigeria", "United States", "U.S.", "US", "USA",
        "Europe", "Asia", "Africa", "North America", "South America",
        "Latin America", "Asia Pacific", "Asia & Pacific Region", "EMEA",
        "APAC", "LATAM", "Rest of World", "Rest of the World",
        "Other European countries", "Other Asia & Pacific countries",
    )
}

# Legal instruments are not entities (the regulator behind them is).
_LEGAL_INSTRUMENT_RE = re.compile(
    r"^(?:the\s+)?(?:(?:amended|second|third|final|proposed|modified)\s+)*"
    r"(?:consent\s+decree|decree|consent\s+agreement(?:\s+and\s+final\s+order)?"
    r"|settlement(?:\s+agreement)?|corporate\s+integrity\s+agreement"
    r"|deferred\s+prosecution\s+agreement|injunction|final\s+order"
    r"|plea\s+agreement|cease[\s-]+and[\s-]+desist\s+order)$",
    re.IGNORECASE,
)

# Vague government collectives — exact match after word-normalization.
_VAGUE_GOVERNMENT = {
    "u s", "us", "usa", "united states", "u s government", "us government",
    "united states government", "federal government", "state government",
    "state governments", "federal and state governments",
    "state and federal governments", "government", "governments",
    "the government", "government agencies", "federal agencies",
    "governmental authorities", "government authorities", "regulators",
    "regulatory authorities", "regulatory agencies",
    "federal state and local governments",
}

# regulatory_precedent counterparties must be a NAMED agency/regulator.
_REGULATOR_ACRONYMS = {
    "EPA", "USEPA", "SEC", "USSEC", "DOJ", "USDOJ", "FDA", "USFDA", "FTC",
    "CFTC", "FCC", "FERC", "OCC", "CFPB", "OSHA", "MSHA", "NHTSA", "FAA",
    "FRA", "PHMSA", "DOT", "USDOT", "DOL", "HHS", "HHSOIG", "OIG", "OIGHHS",
    "CMS", "FINRA", "NRC", "ATF", "DEA", "IRS", "EEOC", "NLRB", "CPSC",
    "OFAC", "FINCEN", "BIS", "USITC", "ITC", "USDA", "NOAA", "CARB", "TCEQ",
    "DTSC", "NYDFS",
}
_REGULATOR_STRUCTURAL_RE = re.compile(
    r"department\s+of|ministry\s+of|environmental\s+protection"
    r"|securities\s+and\s+exchange|food\s+and\s+drug|federal\s+trade"
    r"|federal\s+communications|attorney\s+general|inspector\s+general"
    r"|consumer\s+financial\s+protection|bureau\s+of|office\s+of"
    r"|\bcommission\b|\bagency\b|\badministration\b|\bauthority\b|\bboard\b",
    re.IGNORECASE,
)
# Industry/vertical/sector labels from revenue-by-industry tables — not entities.
_INDUSTRY_COUNTERPARTY_RE = re.compile(
    r"\b(?:industry|industries|sector|sectors|vertical|verticals|market|markets"
    r"|channel|channels|end\s+market|end\s+markets)\s*$",
    re.IGNORECASE,
)


def is_named_regulator(name: str) -> bool:
    acronym = re.sub(r"[^A-Za-z0-9]+", "", name).upper()
    if acronym in _REGULATOR_ACRONYMS:
        return True
    return bool(_REGULATOR_STRUCTURAL_RE.search(name))


def nonentity_counterparty(counterparty_name: str, edge_type: str) -> str | None:
    """Return the specific G2 kill reason, or None if the name is an entity."""
    stripped = counterparty_name.strip()
    if _INDUSTRY_COUNTERPARTY_RE.search(stripped):
        return "g2_industry"
    norm = _norm_words(counterparty_name)
    if _LEGAL_INSTRUMENT_RE.match(stripped):
        return "g2_instrument"
    if norm in _VAGUE_GOVERNMENT:
        return "g2_vague_government"
    if norm in _GEOGRAPHY_NAMES:
        return "g2_geography"
    if edge_type == REGULATORY_PRECEDENT and not is_named_regulator(counterparty_name):
        return "g2_unnamed_regulator"
    return None


# ---------------------------------------------------------------------------
# G3 — anonymous-in-named classifier (taxonomy #7 "Initial Customer",
# #37 Customer "A", #43 "US-based IT OEM"). Descriptor-only labels are
# ANONYMOUS: every token is a generic role noun, descriptor adjective,
# ordinal, bare letter, or number. One genuine proper-name token keeps the
# edge named ("Kura Japan", "U.S. Army", "Columbia Tech").
# ---------------------------------------------------------------------------

_GENERIC_ROLE_TOKENS = {
    "customer", "customers", "client", "clients", "supplier", "suppliers",
    "vendor", "vendors", "distributor", "distributors", "manufacturer",
    "manufacturers", "oem", "oems", "reseller", "resellers", "partner",
    "partners", "operator", "operators", "wholesaler", "wholesalers",
    "retailer", "retailers", "integrator", "provider", "providers",
    "payor", "payors", "payer", "payers", "end-user", "end-users",
}
_DESCRIPTOR_TOKENS = {
    "a", "an", "the", "our", "its", "and", "of", "or",
    "one", "two", "three", "four", "five",
    "initial", "first", "second", "third", "new", "largest", "biggest",
    "top", "major", "key", "primary", "principal", "significant", "leading",
    "single", "sole", "anchor", "certain", "unnamed", "undisclosed",
    "anonymous", "large", "big",
    "us", "u.s", "usa", "american", "foreign", "domestic", "international",
    "european", "asian", "chinese", "japanese", "korean", "german", "french",
    "british", "canadian", "indian", "taiwanese", "global", "multinational",
    "national", "regional", "based", "headquartered",
    "it", "technology", "tech", "software", "hardware", "semiconductor",
    "automotive", "industrial", "healthcare", "financial", "retail", "energy",
}
_QUOTED_PLACEHOLDER_RE = re.compile(
    r"^(?:customer|client|distributor|supplier|operator)\s+[\"“”'’]?[A-Z0-9]{1,2}[\"“”'’]?$",
    re.IGNORECASE,
)


def _anonymous_token(token: str) -> bool:
    t = token.strip("\"“”'’()[],.").lower()
    if not t:
        return True
    if len(t) == 1 or t.isdigit():
        return True  # bare letters ("A") and numbers are labels, not names
    parts = [p for p in t.split("-") if p]
    return all(
        p in _GENERIC_ROLE_TOKENS or p in _DESCRIPTOR_TOKENS or p.isdigit() for p in parts
    )


def is_anonymous_descriptor(counterparty_name: str) -> bool:
    name = counterparty_name.strip()
    if _QUOTED_PLACEHOLDER_RE.match(name):
        return True
    tokens = name.split()
    if not tokens:
        return True
    return all(_anonymous_token(t) for t in tokens)


# ---------------------------------------------------------------------------
# G4 — relationship currency (taxonomy #6 Webull "did not renew",
# #50 WuXi "has historically been... we have moved"). Terminated / historical
# relationships asserted as current are killed. Applies ONLY to
# customer_concentration and sole_supplier; signals are scoped to sentences
# mentioning the counterparty so unrelated boilerplate cannot fire it.
# ---------------------------------------------------------------------------

_TERMINATION_PATTERNS = (
    re.compile(r"\bdid\s+not\s+renew\b", re.IGNORECASE),
    # "the term of the agreement ended on June 14, 2025" — a relationship
    # noun followed by a terminal verb. \bended\b cannot match "year ended
    # December 31" because a relationship noun must precede it in-clause.
    re.compile(
        r"\b(?:agreement|contract|relationship|arrangement|term)\b"
        r"[^.;]{0,60}?\b(?:terminated|ended|expired|lapsed|was\s+not\s+renewed)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:has|have|had)\s+historically\s+been\b", re.IGNORECASE),
    re.compile(r"\bwe\s+have\s+(?:since\s+)?(?:moved|transitioned|shifted)\b", re.IGNORECASE),
    re.compile(r"\b(?:is|are|was|were)\s+no\s+longer\b", re.IGNORECASE),
    re.compile(r"\bno\s+longer\s+(?:our|a|the)\b", re.IGNORECASE),
    re.compile(
        r"\bformer\s+(?:customer|supplier|client|distributor|vendor|partner)\b", re.IGNORECASE
    ),
)


def relationship_terminated(passage: str, counterparty_name: str) -> bool:
    head = _head_token(counterparty_name).lower()
    for sentence in _sentences(passage):
        if head not in sentence.lower():
            continue
        if any(p.search(sentence) for p in _TERMINATION_PATTERNS):
            return True
    return False


# ---------------------------------------------------------------------------
# G5 — magnitude denominator (taxonomy #8 Garden segment, #40 Affiliate
# Marketing segment, #4, #7). A magnitude is only valid against a
# company-level denominator. Segment-possessive or subsidiary ("of its
# revenue") denominators null the magnitude; a customer_concentration edge
# whose ONLY materiality certification was that number dies with it
# (concentration is inherently quantitative — CONCENTRATION_MIN_PCT), while
# sole_supplier edges survive magnitude-less per the reviewer's
# pre-registration (graded on relationship + resolution only).
# ---------------------------------------------------------------------------

_SEGMENT_DENOMINATOR_RE = re.compile(
    r"\bsegment[’']s\s+(?:overall\s+|net\s+|total\s+|operating\s+)*(?:sales|revenues?)\b",
    re.IGNORECASE,
)
_SUBSIDIARY_DENOMINATOR_RE = re.compile(
    r"%\s*of\s+its\s+(?:total\s+)?(?:revenues?|net\s+sales|sales)\b", re.IGNORECASE
)


def magnitude_denominator_scope(passage: str, pct: float) -> str:
    """'company' or 'segment' (segment/subsidiary-scoped denominator)."""
    for sentence in _sentences(passage):
        if not _pct_occurrences(sentence, pct):
            continue
        if _SEGMENT_DENOMINATOR_RE.search(sentence) or _SUBSIDIARY_DENOMINATOR_RE.search(
            sentence
        ):
            return "segment"
    return "company"


# ---------------------------------------------------------------------------
# G6 — latest-year selection (taxonomy #28 NKTR/UCB: claimed 39% = FY2024
# while FY2025 = 14% sat in the same quote). When the quote carries a
# multi-year series, the magnitude must be the LATEST year's figure. Two
# pre-registered detection rules; anything ambiguous is left alone (the
# guard kills only on an affirmative stale pairing).
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PCT_VALUE_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s?%")
_DURING_YEAR_PCT_RE = re.compile(
    r"(?:~|approximately\s+)?(\d{1,3}(?:\.\d+)?)\s?%"
    r"[^.;]{0,80}?\bduring\s+((?:19|20)\d{2})",
    re.IGNORECASE,
)


def _materially_different_pct(claimed: float, latest: float) -> bool:
    """True when a later-year figure is not an immaterial restatement."""
    if abs(claimed - latest) <= 2.0:
        return False
    if claimed > 0 and abs(claimed - latest) / claimed >= 0.10:
        return True
    return abs(claimed - latest) > 2.0


def _prose_during_year_pairs(sentence: str) -> list[tuple[float, int]]:
    return [
        (float(m.group(1)), int(m.group(2)))
        for m in _DURING_YEAR_PCT_RE.finditer(sentence)
    ]


def _distinct_years(text: str) -> list[int]:
    seen: list[int] = []
    for m in _YEAR_RE.finditer(text):
        y = int(m.group(0))
        if 1990 <= y <= 2035 and y not in seen:
            seen.append(y)
    return seen


def _pct_values(text: str) -> list[float]:
    return [float(m.group(1)) for m in _PCT_VALUE_RE.finditer(text)]


def magnitude_stale_year(passage: str, pct: float, counterparty_name: str) -> bool:
    sentences = _sentences(passage)
    mag_sentences = [
        s for s in sentences if any(abs(v - pct) < 1e-9 for v in _pct_values(s))
    ]
    if not mag_sentences:
        return False
    head = _head_token(counterparty_name).lower()
    for s in mag_sentences:
        years = _distinct_years(s)
        pcts = _pct_values(s)
        # Rule B — within-sentence "respectively" pairing of a %-list with an
        # equal-length year-list: the magnitude must map to the latest year.
        if "respectively" in s.lower() and len(years) >= 2 and len(pcts) == len(years):
            idx = next((i for i, v in enumerate(pcts) if abs(v - pct) < 1e-9), None)
            if idx is not None and years[idx] != max(years):
                return True
        # Rule A — the magnitude's sentence is labeled with a single year and
        # ANOTHER sentence quotes the same counterparty with a single LATER
        # year: the claimed figure is stale.
        if len(years) == 1:
            for other in sentences:
                if other is s or head not in other.lower():
                    continue
                other_years = _distinct_years(other)
                if len(other_years) == 1 and _pct_values(other) and other_years[0] > years[0]:
                    return True
        # Rule C — prose "X% during {year} and Y% during {later year}" in one
        # sentence: the magnitude must be the latest year's figure.
        if head in s.lower():
            pairs = _prose_during_year_pairs(s)
            if len(pairs) >= 2:
                latest_year = max(y for _, y in pairs)
                for pair_pct, pair_year in pairs:
                    if abs(pair_pct - pct) < 1e-9 and pair_year < latest_year:
                        latest_pct = next(
                            (p for p, y in pairs if y == latest_year), None
                        )
                        if latest_pct is not None and _materially_different_pct(
                            pct, latest_pct
                        ):
                            return True
    return False


# ---------------------------------------------------------------------------
# G7 — sole_supplier direction + sole language (taxonomy #2 AQST reversed,
# #5 BKE major-vendor-without-exclusivity).
# ---------------------------------------------------------------------------

_EXCLUSIVITY_RE = re.compile(
    r"\b(?:sole|sole[-\s]source|single[-\s]source"
    r"|only\s+(?:source|supplier|manufacturer|provider|vendor)"
    r"|exclusive(?:ly)?|solely)\b",
    re.IGNORECASE,
)
_ALL_OF_OUR_RE = re.compile(
    r"\ball\s+(?:of\s+)?(?:our|its|the)\b[^.;]{0,100}"
    r"\b(?:manufactured|purchased|supplied|sourced|produced|obtained|assembled)\b"
    r"|\b(?:purchase|source|obtain|buy)\s+all\s+of\s+(?:our|its|the)\b",
    re.IGNORECASE,
)
# Filer (we / a non-counterparty proper name) as subject of sole-supply language
# means the counterparty is a customer, not the filer's supplier.
_FILER_AS_SUPPLIER_RE = re.compile(
    r"\b(?:we|our company)\s+(?:will serve as|is|are|was|were)\s+"
    r"(?:the\s+)?(?:exclusive\s+)?(?:sole\s+)?"
    r"(?:manufacturer and supplier|manufacturer|supplier)\s+of\b",
    re.IGNORECASE,
)
_NAMED_FILER_AS_SUPPLIER_RE = re.compile(
    r"\b([A-Z][A-Za-z]+(?:ive| Inc)?)\s+(?:will serve as|is|are|was|were)\s+"
    r"(?:the\s+)?(?:exclusive\s+)?(?:sole\s+)?"
    r"(?:manufacturer and supplier|manufacturer|supplier)\s+of\b",
    re.IGNORECASE,
)


def _counterparty_is_supplier_subject(passage: str, counterparty_name: str) -> bool:
    """True when the counterparty (not the filer) is named as the supplier."""
    head = re.escape(_head_token(counterparty_name))
    return bool(
        re.search(
            rf"\b{head}\b[^.;]{{0,40}}?\b(?:will serve as|is|are|was|were)\s+"
            rf"(?:the\s+)?(?:exclusive\s+)?(?:sole\s+)?(?:manufacturer|supplier)\b",
            passage,
            re.IGNORECASE,
        )
    )


def sole_supplier_violation(passage: str, counterparty_name: str) -> str | None:
    """G7 verdict for a sole_supplier edge: kill reason or None."""
    head = re.escape(_head_token(counterparty_name))
    head_plain = _head_token(counterparty_name)
    # Direction: an exclusivity phrase whose beneficiary ("for X" / "to X")
    # is the counterparty means the FILER is the supplier — reversed edge.
    if re.search(
        rf"\b(?:sole|exclusive|single[-\s]source|only)\b[^.;]{{0,80}}?"
        rf"\b(?:for|to)\s+(?:the\s+)?{head}\b",
        passage,
        re.IGNORECASE,
    ):
        return "g7_direction"
    if not _counterparty_is_supplier_subject(passage, counterparty_name):
        if _FILER_AS_SUPPLIER_RE.search(passage):
            return "g7_direction"
        for m in _NAMED_FILER_AS_SUPPLIER_RE.finditer(passage):
            if m.group(1).lower() != head_plain.lower():
                return "g7_direction"
    if not (_EXCLUSIVITY_RE.search(passage) or _ALL_OF_OUR_RE.search(passage)):
        return "g7_sole_language"
    return None


# ---------------------------------------------------------------------------
# G8 — counterparty is the filer's own product (round 4 #7 AUPH/LUPKYNIS).
# ---------------------------------------------------------------------------

_FILER_PRODUCT_RE = (
    r"\b{name}\b[^.;]{{0,50}}?\bour only (?:approved )?product\b"
    r"|\bour only (?:approved )?product[^.;]{{0,50}}?\b{name}\b"
    r"|\b{name}\b[^.;]{{0,40}}?\b(?:our|the) (?:lead|sole) product(?: candidate)?\b"
    r"|\b{name}\b[^.;]{{0,120}}?\bour only source of (?:net )?product sales\b"
    r"|\bour only source of (?:net )?product sales[^.;]{{0,80}}?\b{name}\b"
)


def is_filer_product(counterparty_name: str, passage: str) -> bool:
    """True when the counterparty is the filer's drug/product, not a supplier."""
    esc = re.escape(counterparty_name.strip())
    return bool(re.search(_FILER_PRODUCT_RE.format(name=esc), passage, re.IGNORECASE))


# ---------------------------------------------------------------------------
# G9 — hypothetical regulatory boilerplate (round 4 #50 ZURA/FDA). Requires
# an actual, attributable filer action for regulatory_precedent edges.
# ---------------------------------------------------------------------------

_HYPOTHETICAL_REG_RE = re.compile(
    r"\b(may\s+include|could\s+include|might\s+include|would\s+include"
    r"|may\s+result\s+in|frequently\s+includes?|for\s+example|such\s+as)\b",
    re.IGNORECASE,
)
_ACTUAL_REG_ACTION_RE = re.compile(
    r"\b(?:we|the\s+company|our\s+company)\s+"
    r"(?:entered\s+into|received|agreed\s+to|settled)\b"
    r"|(?:entered|enter)\s+into\s+a\s+(?:settlement|consent\s+decree|consent\s+agreement"
    r"|consent\s+agreement\s+and\s+final\s+order)"
    r"|consent\s+decree,?\s+dated\b"
    r"|\$\s*[\d,]+"
    r"|\bOn\s+(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},?\s+(?:19|20)\d{2}\b"
    r"|\bin\s+(?:19|20)\d{2},?\s+the\s+(?:company|we)\b",
    re.IGNORECASE,
)


def is_hypothetical_regulatory(passage: str) -> bool:
    """True when a regulatory edge cites only hedged/hypothetical enforcement."""
    if not _DECREE_TERM_RE.search(passage):
        return False
    return bool(_HYPOTHETICAL_REG_RE.search(passage)) and not _ACTUAL_REG_ACTION_RE.search(
        passage
    )


# ---------------------------------------------------------------------------
# Composition — the full guard stack (rounds 1-3) over a stored edge.
# ---------------------------------------------------------------------------


@dataclass
class GuardDecision:
    """Outcome of running the guard stack on one edge.

    action: "keep" | "kill" | "demote" (demote = named -> anonymous, edge kept)
    guard: the kill guard (action == "kill") or None
    demoted_by: the demotion guard, set even when the edge is also killed
    null_magnitude/denominator_scope: G5 metadata adjustments for survivors
    """

    action: str
    guard: str | None = None
    demoted_by: str | None = None
    null_magnitude: bool = False
    denominator_scope: str | None = None


def apply_round3(
    *,
    edge_type: str,
    counterparty_name: str,
    named: bool,
    magnitude_pct: float | None,
    quoted_passage: str,
) -> GuardDecision:
    """Round-3 guards (G1-G7) in pre-registered order; first kill wins."""
    demoted_by = None
    if named and is_anonymous_descriptor(counterparty_name):
        demoted_by = "g3_anonymous_descriptor"

    if is_internal_entity(counterparty_name, quoted_passage):
        return GuardDecision("kill", "g1_internal_entity", demoted_by=demoted_by)
    g2 = nonentity_counterparty(counterparty_name, edge_type)
    if g2 is not None:
        return GuardDecision("kill", g2, demoted_by=demoted_by)
    if edge_type == REGULATORY_PRECEDENT and is_hypothetical_regulatory(quoted_passage):
        return GuardDecision("kill", "g9_hypothetical_regulatory", demoted_by=demoted_by)
    if edge_type in (CUSTOMER_CONCENTRATION, SOLE_SUPPLIER) and relationship_terminated(
        quoted_passage, counterparty_name
    ):
        return GuardDecision("kill", "g4_relationship_currency", demoted_by=demoted_by)
    if edge_type == SOLE_SUPPLIER:
        if is_filer_product(counterparty_name, quoted_passage):
            return GuardDecision("kill", "g8_product", demoted_by=demoted_by)
        g7 = sole_supplier_violation(quoted_passage, counterparty_name)
        if g7 is not None:
            return GuardDecision("kill", g7, demoted_by=demoted_by)

    null_magnitude = False
    denominator_scope = None
    magnitude = magnitude_pct
    if magnitude is not None and magnitude_denominator_scope(quoted_passage, magnitude) == "segment":
        if edge_type == CUSTOMER_CONCENTRATION:
            return GuardDecision("kill", "g5_denominator", demoted_by=demoted_by)
        null_magnitude = True
        denominator_scope = "segment"
        magnitude = None
    if magnitude is not None and magnitude_stale_year(
        quoted_passage, magnitude, counterparty_name
    ):
        return GuardDecision("kill", "g6_stale_year", demoted_by=demoted_by)

    if demoted_by:
        return GuardDecision(
            "demote",
            demoted_by=demoted_by,
            null_magnitude=null_magnitude,
            denominator_scope=denominator_scope,
        )
    return GuardDecision(
        "keep", null_magnitude=null_magnitude, denominator_scope=denominator_scope
    )


def apply_edge_guard_stack(
    *,
    edge_type: str,
    counterparty_name: str,
    named: bool,
    magnitude_pct: float | None,
    quoted_passage: str,
) -> GuardDecision:
    """Full stack: rounds 1-2 (re-applied; idempotent for stored edges) then
    round 3. Used by the 3c-prep-2 re-filter and by the graph builder for any
    future inserts. Logs every kill with the responsible guard's name."""
    decision: GuardDecision | None = None
    if edge_type == CUSTOMER_CONCENTRATION and magnitude_pct is not None:
        if not revenue_basis_certified(quoted_passage, magnitude_pct):
            decision = GuardDecision("kill", "r1_revenue_basis")
        elif aggregate_group_pct(quoted_passage, magnitude_pct):
            decision = GuardDecision("kill", "r1_aggregate_group")
        elif supplier_direction(quoted_passage, counterparty_name):
            decision = GuardDecision("kill", "r1_supplier_direction")
    if decision is None and edge_type == REGULATORY_PRECEDENT and not operative_decree(
        quoted_passage
    ):
        decision = GuardDecision("kill", "r1_operative_decree")

    r12_demotion = None
    if named and is_anonymous_placeholder(counterparty_name):
        r12_demotion = "r1_anonymous_placeholder"
    elif named and is_counterparty_category(counterparty_name):
        r12_demotion = "r2_counterparty_category"

    if decision is None:
        decision = apply_round3(
            edge_type=edge_type,
            counterparty_name=counterparty_name,
            named=named and r12_demotion is None,
            magnitude_pct=magnitude_pct,
            quoted_passage=quoted_passage,
        )
        if r12_demotion is not None:
            decision.demoted_by = decision.demoted_by or r12_demotion
            if decision.action == "keep":
                decision.action = "demote"
    elif r12_demotion is not None:
        decision.demoted_by = r12_demotion

    if decision.action == "kill":
        logger.info(
            "guard kill [%s]: [%s] -> %s | %s",
            decision.guard,
            edge_type,
            counterparty_name,
            (quoted_passage[:80] + "...") if len(quoted_passage) > 80 else quoted_passage,
        )
    return decision
