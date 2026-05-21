# PR #7 Follow-up Review Documents Meta Review

- **Review date**: 2026-05-21
- **Branch**: `fix/turtle-v015-review-followups`
- **Scope**:
  - `docs/tech_reviews/2026-05-21-pr7-turtle-v015-value-analyst-review.md`
  - `docs/tech_reviews/2026-05-21-pr7-turtle-calculation-and-source-review.md`
  - `docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md`
- **Reference spec**: `docs/superpowers/specs/2026-05-21-turtle-correctness-fixes-design.md`
- **Method**: three independent subagent reviews, one per document, followed by cross-document reconciliation.

## Summary

The three 2026-05-21 tech review documents are useful as the original PR #7 review record. They identify the important correctness, source-traceability, and observability gaps that later became Spec 1.

They should not be treated as current execution instructions without updates. The latest Spec 1 has resolved several open design choices, added stricter payload persistence semantics, and corrected the backend propagation model. The roadmap in particular is stale enough to mislead implementation planning.

## Findings

### 1. Roadmap Status Is Stale

`2026-05-21-pr7-turtle-v015-followup-roadmap.md` still marks Spec 1 as not started, with no spec document and "waiting user" status (`lines 64, 130`). Current Spec 1 already exists at `docs/superpowers/specs/2026-05-21-turtle-correctness-fixes-design.md` and identifies itself as the Spec 1 design.

**Impact**: future implementers may restart brainstorming or miss the accepted Spec 1 contract.

**Recommended update**: mark Spec 1 as spec-approved, add the spec path, and replace "waiting user" with "Spec 1 design written; plan/implementation pending" or the current actual state.

### 2. Roadmap Spec 1 Scope Is Incomplete

The roadmap's Spec 1 mapping omits items now required by Spec 1:

- explicit `holding_channel` handling that stops unconditional `DEFAULT_CHANNEL_CAVEAT`
- report-side payout proxy key-collision fix
- `AgentState` schema extension for `value_turtle_payload`
- empty payload persistence behavior
- persistence-specific unit coverage replacing the old smoke-file assertion

**Impact**: the roadmap understates the implementation surface and can produce an incomplete plan.

**Recommended update**: sync roadmap Spec 1 bullets with the current spec's scope table and backend payload section.

### 3. Roadmap Still Contains Superseded Options

Several roadmap bullets still describe options that Spec 1 has already decided:

- redaction is described as selective cleanup, while Spec 1 deletes redaction entirely
- `facts.status` is described as key-field inference, while Spec 1 chooses adapter-emitted status plus `merge_status`
- A.5 still says "support degraded or delete", while Spec 1 chooses deletion of misleading degraded branches
- B.5 is mapped loosely to Spec 1 or Spec 4, while Spec 1 explicitly excludes B.4/B.5 from Spec 1

**Impact**: implementers may follow rejected alternatives.

**Recommended update**: preserve the old options as historical context only, or replace them with the chosen decisions.

### 4. Calculation Review Misses the Payout Key Collision

`2026-05-21-pr7-turtle-calculation-and-source-review.md` correctly flags that the report-side payout proxy is a single-year value named as a 3-year average (`B.3`). It does not spell out the more serious collision: report and market can both write `dividend_avg_payout_ratio_3y`, and calculations prefer report fields before market fields.

**Impact**: the review under-explains why a rename is necessary rather than merely cosmetic.

**Recommended update**: add the report-first lookup chain and market true-3-year overwrite risk to B.3.

### 5. Calculation Review Backend Payload Section Is Stale

The calculation review's D.3 backend proposal says `trading_graph.py:870` should propagate `value_turtle_payload`. In current code that location is `_log_state`, not the main propagation return path. Spec 1 corrected this: propagation is automatic after `AgentState` and InitialState include the field; `_log_state` is optional logging only.

The same section also omits:

- `AgentState` TypedDict extension
- `value_analyst_node` always returning the payload key on report-producing paths
- skipping empty strings during persistence
- a dedicated persistence unit test

**Impact**: implementation based on the review alone would modify the wrong layer and miss required state/persistence contracts.

**Recommended update**: align D.3 with Spec 1 section 6.

### 6. Calculation Review A.5 Headline Is Imprecise

The review says `ev_switch` / `cash_protection` degraded branches are never triggered. The branch can be reached when `cash` or `interest_bearing_debt` is missing while `market_cap` is present; the problem is that the result has `status="degraded"` while `value=None`.

**Impact**: the diagnosis is slightly wrong even though the proposed cleanup remains useful.

**Recommended update**: rename the issue to "degraded status with no computable value" or "misleading degraded branch", then explain why Spec 1 deletes it.

### 7. Calculation Review A.1 Needs More Precise Model Language

A.1 frames `3y payout ratio * current profit` as a model error. It may also be interpreted as a normalized payout assumption. If the intended metric is trailing shareholder yield, the document should also align buyback horizon and market-cap snapshot.

There is also a wording bug: it says "把分母换成当期分红率", but the denominator is `market_cap`; the intended phrase appears to be "把 payout 换成当期分红率".

**Impact**: this can overstate certainty around a financial-model decision and confuse the proposed fix.

**Recommended update**: reframe A.1 as a required model decision, not a settled bug, and fix the denominator wording.

### 8. Value Analyst Review Overstates Redaction Runtime Impact

`2026-05-21-pr7-turtle-v015-value-analyst-review.md` says `build_non_decisionable_report` is the default fallback once the analyst sees `signals.status == "non_decisionable"`. Current `value_analyst.py` instead hydrates the Turtle payload and builds an LLM prompt with `build_turtle_decision_prompt`; `build_non_decisionable_report` is not called in the analyst path.

**Impact**: the redaction bug is real, but its production path is overstated.

**Recommended update**: describe it as a deterministic fallback/helper bug covered by tests and exports, not the current default runtime path.

### 9. Value Analyst Review Overstates Dataclass Immutability

The review says frozen dataclasses plus `__post_init__` deep copies guarantee immutability. The data structures expose mutable `dict` and `list` fields. The code makes defensive copies at construction and serialization boundaries, but callers can still mutate contained dictionaries/lists.

**Impact**: this overstates safety guarantees.

**Recommended update**: replace "保证不可变性" with "降低外部 aliasing 风险，但不是深度不可变".

### 10. Value Analyst Review Status Fix Conflicts With Spec 1

The value analyst review suggests deriving `facts.status` from caveats and missing critical money fields. Spec 1 rejects a critical-field whitelist at facts level and chooses adapter-emitted source status plus signal-level formula decisionability.

**Impact**: the review points toward a superseded heuristic.

**Recommended update**: add a note that Spec 1 supersedes this repair idea with adapter-emitted status and `merge_status`.

### 11. Minor Stale Counts and References

The value analyst review says 76 unit tests pass, while collect-only on this branch over Turtle/value entry modules reports 102 tests. Some code line references in the calculation review are also imprecise, including formula and market adapter locations.

**Impact**: low; the documents remain understandable, but exact references are no longer reliable.

**Recommended update**: either refresh counts/line references or label them as references to merge commit `ca6fa00`.

## Recommended Next Edits

1. Update the roadmap first. It is the highest-risk stale artifact because it drives planning.
2. Add "superseded by Spec 1" notes to the two review documents instead of rewriting them wholesale. They are valuable as historical review records.
3. Fix the calculation review's B.3, D.3, and A.5 sections because those are most likely to produce wrong implementation work.
4. Fix the value analyst review's redaction-impact and `facts.status` repair-language paragraphs.
5. Decide whether line numbers should be refreshed or explicitly scoped to commit `ca6fa00`.
