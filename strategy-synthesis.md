# Synthesis Memo — Two Engines and a Lens

*Written after all three independent memos (Opus 4.7: catalyst scanner; Opus 4.8: refined-PEAD + propagation graph; Fable 5: Second Reader). The question: taking the best ideas from each, is there a stronger combined design? I believe yes — not by running all the bots side by side, but by noticing that several of them are the same trade wearing different clothes.*

---

## The two unification insights

### Insight 1: PEAD-riding and overreaction-fading are one question, not two bots

4.8's Bot A asks: *did the market underreact to this earnings surprise?* (If yes, ride the drift.)
My triage sleeve asks: *did the market overreact to this crash?* (If yes, fade the panic.)

Strip the framing and both ask the identical question: **after a shock, is the market's reaction proportionate to the fundamental dollar change?** One question, three answers:

- Reaction **<** fundamental change → underreaction → **ride** (the PEAD trade)
- Reaction **≈** fundamental change → proportionate → **no trade**
- Reaction **>** fundamental change → overreaction → **fade** (the triage trade)

Merging them into one **Proportionality Engine** is strictly stronger than running them as separate bots:

1. **Same plumbing.** Event detection, source-document retrieval, fundamental-delta estimation, reaction measurement — 80% shared. Two bots would build it twice.
2. **A null class.** A bot that can only ride drift sees drift everywhere; a bot that can only fade panic sees panic everywhere. An engine forced to classify into {ride, nothing, fade} has "proportionate — no trade" as its default, and most events should land there. This is the single biggest epistemic upgrade over any of the three memos.
3. **Double the calibration signal.** Every proportionality estimate gets scored whichever way the event resolves. The ledger learns from both tails of the same judgment.
4. **Measurement stays separable.** Merging code ≠ merging evaluation: the calibration ledger buckets by (event type × direction), so ride-side and fade-side still compete in the champion/challenger harness like separate bots would. You lose nothing.

4.7's contribution lives here too: its catalyst classes (contract awards, FDA actions, 8-Ks, insider clusters) stop being their own bot — they become **event types feeding the same engine**. 4.7's real asset was always the watcher plumbing, not the strategy.

### Insight 2: The propagation graph and the polyglot desk are the same trade

4.8's propagation: *information has arrived at company A; neighbor N's price hasn't absorbed it.*
My polyglot desk: *information has arrived in Tokyo; the US-listed line hasn't absorbed it.*

Same abstract structure: **locate the information frontier, trade the security the wave hasn't reached yet.** Call it the **Frontier Engine**. And the merge fixes the biggest weakness in both parents — *who certifies that the information is material?*

- In the polyglot variant, **the home market certifies it**: the local line moved 4% on the disclosure. No LLM materiality guess needed.
- In the customer-concentration variant (4.8's narrowed graph), **the SEC filing certifies it**: the target disclosed ≥20% revenue dependency on the shocked company. The magnitude is on paper.

So the synthesis rule: **the Frontier Engine only trades lags where materiality is proven by something other than the LLM's opinion** — a price print in another market, or a disclosed dependency percentage. The LLM's job shrinks to what it's reliable at: reading the foreign filing, mapping the relationship, and running the adversarial "has the target already moved?" gate. The full free-form propagation graph (soft edges, narrative links) stays a *research* layer that proposes candidates; it earns trading rights only if its ledger buckets calibrate.

---

## The third element: the Flow Lens

My synthetic-crowd idea (simulate consumer LLMs daily, predict retail-agent flow) doesn't merge into either engine — it's orthogonal, which is exactly its value. It enters the design in two stages:

**Stage 1 (immediately, ~$3/day, zero capital): an overlay on both engines.** Every fundamental strategy shares one failure mode — being right about value while flow runs you over. The lens addresses it cheaply:
- Proportionality Engine wants to **fade** a name that is simultaneously *entering* multi-model consensus → **defer**: incoming agent flow will push the overshoot further before it reverts.
- Engine wants to **ride** a name with consensus-onset tailwind → normal size, better timing confidence.
- Names at consensus *saturation* → tighten exits.

**Stage 2 (only if measurement validates): its own strategy.** If 4–8 weeks of logs show consensus-onset predicts abnormal volume in tail names, the flow signal graduates to placing its own trades through the same Strategy protocol. Until then it never touches capital.

Either way, **start the logging harness now.** It's the only component in any memo where waiting permanently destroys data — every unlogged week of the early agentic era is unrecoverable.

---

## What survives from each memo, and what dies

| Source | Survives into synthesis | Dies (and why) |
|---|---|---|
| 4.7 | Watcher/event plumbing; the undercovered small-cap universe thesis | Catalyst classes as a standalone strategy (speed-reading public feeds is the most contested ground; becomes event *input* instead) |
| 4.8 | PEAD engine (as the ride side); customer-concentration links (as a Frontier proof source); **calibration gating**; ensemble disagreement as confidence; deterministic state | Free-form propagation graph as a *trading* system (materiality guessing was its weak joint; demoted to research/candidate-generation) |
| Fable 5 | Overreaction triage (as the fade side); polyglot lag (as the other Frontier proof source); flow lens; cheap-falsification ordering; crumbs scanner | Four-sleeve structure (fragmentation was its weak joint; sleeves dissolve into the engines) |

Plus the meta-rule that governs everything, composed from 4.8's gating and my validation ordering: **capital follows calibration.** Every (engine × event type × direction) bucket starts at zero allocation, paper-trades, and earns real capital only through its measured ledger — and buckets get killed as written, not renegotiated after a hot week.

---

## How this maps onto the repo (answering "can it be Bot C?")

Almost nothing already built is wasted; several things get *simpler*:

- **Bot A (PEAD, Phase 2, in flight)** → becomes the Proportionality Engine's **ride side**. Build continues as planned; the fade side is added to the *same* strategy class later, not as a new bot. The 2b mechanical-drift checkpoint and 1.5h (real surprise data) remain exactly as blocking as they were.
- **"Bot C" (triage)** → **not a separate bot.** It's the fade classification of the same engine — shared event pipeline, shared rubric, shared ledger. (Scope note: start with *close-to-close* overreactions — gap fades at next open — so the current daily-cadence feed suffices; intraday crash detection is a later infrastructure upgrade, not a prerequisite.)
- **Bot B (propagation, Phase 3)** → becomes the **Frontier Engine**, built in two proof-source stages: customer-concentration links first (pure EDGAR, no new feeds), polyglot/ADR lag second (needs foreign feeds + OpenFIGI mapping). The graph builder (3a) survives; its trading scope narrows to proven-materiality edges.
- **Flow Lens** → a new, small, standalone logging harness outside the trading loop. Not a Strategy. ~200 lines + a cron. Start immediately.
- **Crumbs scanner** → a cron job + notification, not a bot. 99-share positions don't need the harness.
- **Champion/challenger (Phase 4)** → unchanged, and now richer: it compares ledger buckets (ride vs fade vs frontier-ADR vs frontier-concentration) rather than just Bot A vs Bot B.

---

## Why I believe the synthesis is stronger than any single memo

1. **Less surface area than my four sleeves, more coverage than 4.8's two bots** — two engines + one harness + one cron.
2. **The null class.** No single-direction bot in any memo had one. Most events deserve no trade; the synthesis is the only design whose default answer is "nothing."
3. **Materiality is externally certified in the Frontier Engine** — the weakest joint in both 4.8's propagation (LLM guesses sign/magnitude) and 4.7's catalyst scoring (LLM guesses materiality) is replaced by price prints and disclosed percentages.
4. **The LLM is assigned only its reliable skills** — reading, extraction, translation, relationship-mapping, adversarial checking — while dollar-delta anchoring is mechanical wherever possible and every judgment passes through calibration gating before touching capital.
5. **Flow-awareness at near-zero cost** addresses the failure mode all fundamental strategies share.

**Honest weaknesses that remain:** the proportionality judgment is still the hardest LLM task in the system (mitigated by mechanical anchors, ensemble disagreement, and the null-class default — but not eliminated); merging ride/fade into one engine is more design work up front than two dumb bots; real slippage on thin names remains the unkillable unknown that only live fills resolve; and the Flow Lens may simply measure nothing tradable (acceptable — it's $150 to find out).

---

## Revised build order (minimal disruption to current phases)

1. **Now, in parallel with 1.5h:** stand up the Flow Lens logging harness (no trading, no dependency on anything in-flight) and the crumbs cron.
2. **Phase 2 (unchanged):** finish Bot A ride-side exactly as planned — 1.5h → 2b drift checkpoint → quality scoring → forward paper.
3. **Phase 2.5 (new, small):** add the fade classification to the same engine — close-to-close overreactions only, same ledger, new buckets.
4. **Phase 3 (rescoped):** Frontier Engine, customer-concentration stage first (pure EDGAR), ADR/polyglot stage second.
5. **Phase 4 (unchanged):** champion/challenger across all ledger buckets; capital follows calibration.
6. **Phase 5 (unchanged):** the winners go live exactly as already planned.
