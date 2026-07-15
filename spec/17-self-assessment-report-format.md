## §17. Self-Assessment Report Format

The V1 Stage 12 report member is `report.md`. A later §30 mirror may render the same projection as local HTML, but `report.html` is not a V1 managed member.

Default output:

```markdown
# Self-Assessment Snapshot

Snapshot created: <AssessmentSnapshot.created_at as its stored offset-aware ISO 8601 value>
Scope: <AssessmentSnapshot.scope; AssessmentScope is defined only in §10>
Scope target: <AssessmentSnapshot.scope_target when present>

## 1. Summary

## 2. Strongly Supported Facts

## 3. Recurring Signals

## 4. Current Strengths

## 5. Weakly Supported Strengths

## 6. Gaps

## 7. Contradictions

## 8. Risks / Failure Modes

## 9. Unknowns

## 10. Questions Worth Answering

## 11. Evidence Map

## 12. Counterevidence
```

The section projection is deterministic. The unique `narrative_summary` claim renders only in Summary. Every other snapshot claim receives exactly one primary section by the first matching rule below; the §16.11 status label remains adjacent to its prose:

| Precedence | Selection | Primary section |
|---:|---|---|
| 1 | `dimension = "gap"` (§10) | Gaps |
| 2 | `dimension` is `risk` or `constraint` (§10) | Risks / Failure Modes |
| 3 | §16.11 status is `contradicted` | Contradictions |
| 4 | §16.11 status is `needs_clarification` | Unknowns |
| 5 | `claim_kind = "pattern_signal"` (§10) | Recurring Signals |
| 6 | §16.11 status is `supported` | Current Strengths |
| 7 | §16.11 status is `partially_supported` or `inferred_but_acceptable` | Weakly Supported Strengths |

Any non-summary claim not selected by that closed mapping makes the report invalid rather than being hidden or placed heuristically. Strongly Supported Facts is not a second claim-placement channel: it renders the current `ExperienceFact.claim` values that are reached directly or through a signal from at least one rendered `supported` claim and whose `Confidence` is the maximum member under §10, with the fact ID and supporting claim IDs visible. Questions Worth Answering and Counterevidence remain the typed projections below; Evidence Map renders only the §13.12 typed link projections, never explanatory factual prose.

The tone should be:

```text
clear
specific
non-flattering
non-punitive
evidence-aware
```

Fixed headings always render in the order shown above. Within a primary claim section, claims sort by `SelfClaim.id` ascending in UTF-8 byte order; Strongly Supported Facts sort by `ExperienceFact.id`; typed unknowns and contradictions sort by their own IDs; and counterevidence sorts by claim ID, then (`source_ref_type`, `source_ref_id`) UTF-8 bytes. In the mixed Unknowns and Contradictions sections, the status-selected primary `SelfClaim` rows render first in claim-ID order, followed respectively by referenced `GapQuestion` or `Contradiction` rows in their own ID order; the classes never interleave. A section with no selected row renders its heading and no filler sentence, placeholder, synthetic summary, or inferred transition. `Snapshot created` comes only from the persisted snapshot and never from export wall-clock time. These rules plus §13.12's JSON ordering make repeated rendering of the same coherent snapshot byte-identical.

Every nonliteral value inserted into Markdown, other than a validated source-voice excerpt, uses one deterministic escaping function. It normalizes line endings to LF and Unicode to NFC, then backslash-escapes every ASCII punctuation code point in this fixed set on each logical line. Fixed title/heading labels and Markdown syntax are renderer-owned structural literals emitted exactly as specified and do not pass through this function:

```text
!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~
```

A generated tab is emitted as the structural character reference `&#9;`; embedded generated line breaks are joined with a service-authored Markdown hard break and the continuation indentation required to keep the value in its current block or list item. Generated and structural segments use UTF-8 and LF, and the complete file ends in exactly one LF. A validated source-voice excerpt is instead isolated in its own deterministic fenced block: the fence is the shortest backtick run of at least three code points longer than every backtick run in the excerpt, and the interior source bytes — including its original newline bytes — are emitted unchanged. The source interior is the sole exception to whole-member LF normalization and generated-voice NFC normalization. Structural escaping or fencing never supplies factual words. Missing escape containment, a value that changes section/block structure, or a source excerpt whose unescaped value no longer validates byte-for-byte under §16.12 fails rendering closed.

Every rendered `SelfClaim` keeps its §16.11 status visible. The Summary renders the snapshot's required `narrative_summary` claim, so it is governed by the same status gate. `partially_supported` and `inferred_but_acceptable` content must not appear under Strongly Supported Facts; `needs_clarification` renders as uncertainty or a question; and `contradicted` renders with its contradiction and counterevidence. A snapshot outside the §16.11 assessment-export allowlist does not export.

Every rendered `OccurredAt` preserves its stated precision under §11.1 and never renders a date or time narrower than that precision supports. A calendar-aligned value normalized from a named period may render as its ISO week, month, quarter, or year label. A legal non-aligned value retains its original anchor and precision: the renderer must not re-align or relabel it as a named calendar period, and any displayed anchor is explicitly labeled representational rather than an exact occurrence date. `date_range` and `approximate_range` remain visibly ranges, and an approximate range remains visibly approximate. The maximum-uncertainty intervals in §16.7 are comparison semantics, not permission to render a narrower date.

The Gaps section renders status-bearing snapshot claims with `dimension = "gap"`. The Unknowns section renders no free-form snapshot prose: for every ID in the snapshot's `gap_question_ids` — complete and unanswered as of synthesis — it presents the referenced `GapQuestion.target_*`, `reason`, and `priority` as missing-information context. A question answered after synthesis renders with an explicit answered-since-synthesis marker; its answer reaches the model only through extraction and the next generation. Questions Worth Answering renders only the still-unanswered rows' `question` values. Missing, duplicate, or superseded IDs fail before rendering under §12 rule 10; a post-synthesis answer is visible state, never a rendering or export failure. These references present uncertainty; they do not receive a status, alter §16.11 aggregation, or become independent inputs to Stage 10. Any declarative conclusion must instead be a status-visible `SelfClaim`.

Recurring Signals may render signal-derived language only through a current `SelfClaim` referenced by the snapshot, with that claim's status and source mapping intact. The report must not dump `SelfSignal` rows as independently reviewed conclusions.

The Contradictions section renders every contradiction referenced by the selected current snapshot, including its title, description, and both typed source references. There is no resolved/dismissed filter or resolution note. Superseded contradictions appear only with historical snapshot inspection, never through current export.

The Counterevidence section renders every non-empty `SelfClaim.counterevidence` list for the snapshot, not only lists on `contradicted` claims. Each block identifies the claim, keeps its §16.11 status visible, and labels the entries as verifier-grounded contrary-evidence annotations. Each entry renders its `statement` with its typed (`source_ref_type`, `source_ref_id`) grounding reference so the reader can navigate from the annotation to the persisted source; rendering re-validates resolvability like every typed reference, while closure membership and duplicate rejection were already enforced at the §13.7 write boundary. Counterevidence is not a separate claim, cannot improve a claim or snapshot status, and cannot guide Stage 10 as an independent prose channel.

All Exp2Res-authored report prose is generated voice under §16.12. A quoted Evidence Map segment retains source voice only when it carries a typed source ID and is verified byte-for-byte as the referenced persisted value or a contiguous substring; otherwise it is generated voice. The renderer must not merge a validated source segment with generated prose before voice validation.

---
