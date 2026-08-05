## §17. Self-Assessment Report Format

- The V1 Stage 12 report members are `report.md` and `report.html`: one projection emitted in two forms.
- Both render from the same deterministic section model in the same export transaction and therefore always carry the same content, in the same order, with the same values.
- Neither is produced from the other's bytes, so the HTML member is never a Markdown conversion, inherits no Markdown escape sequence, and needs no compensating unescape step.
- `report.md` is the canonical plain-text surface an editor or terminal reads directly.
- `report.html` is the reading surface a browser opens from the filesystem, and the §30 mirror view serves that same rendering rather than a second one.

Default output:

```markdown
# Self-Assessment Snapshot

Snapshot created: <AssessmentSnapshot.created_at as its stored offset-aware ISO 8601 value>
Scope: <AssessmentSnapshot.scope; AssessmentScope is defined only in §10>
Scope target: <AssessmentSnapshot.scope_target when present>

## 1. Summary

## 2. Strongly Supported Facts

## 3. Recurring Patterns and Interests

## 4. Current Strengths

## 5. Weakly Supported Strengths

## 6. Gaps

## 7. Contradictions

## 8. Risks / Failure Modes

## 9. Unknowns and Open Questions
```

The section projection is deterministic. The unique `narrative_summary` claim renders only in Summary. Every other snapshot claim receives exactly one primary section by the first matching rule below; the §16.11 status label remains adjacent to its prose:

| Precedence | Selection | Primary section |
|---:|---|---|
| 1 | `dimension = "gap"` (§10) | Gaps |
| 2 | `dimension` is `risk` or `constraint` (§10) | Risks / Failure Modes |
| 3 | §16.11 status is `contradicted` | Contradictions |
| 4 | §16.11 status is `needs_clarification` | Unknowns and Open Questions |
| 5 | `dimension` is `domain_interest`, `working_style`, `trajectory`, or `identity_hypothesis` (§10) | Recurring Patterns and Interests |
| 6 | §16.11 status is `supported` | Current Strengths |
| 7 | §16.11 status is `partially_supported` or `inferred_but_acceptable` | Weakly Supported Strengths |

- Any non-summary claim not selected by that closed mapping makes the report invalid rather than being hidden or placed heuristically.
- Section selection keys on what a claim characterizes — its `dimension` — and how well the evidence supports it — its §16.11 status.
- Section selection never keys on `claim_kind`, which records synthesis form under §15.4; the unique `narrative_summary` placement above is the only kind-driven rule.

  A kind-keyed row would route claims by how the writer derived them: because the §13.6 writer legitimately derives most claims from recurring patterns, such a row drains the strength sections into one bucket regardless of what each claim asserts.
- Over the statuses the §16.11 assessment-export allowlist admits, the mapping is total:
  - Rules 1 and 2 place the `gap`, `risk`, and `constraint` dimensions at any status.
  - Rule 5 places the four orientation dimensions at any status rules 3 and 4 leave.
  - The two capability dimensions — `technical_skill` and `execution_capacity` — fall through to rules 3, 4, 6, and 7, which together cover every admitted status.
  - The invalid-report rule remains the defense against out-of-contract state.
- Strongly Supported Facts is not a second claim-placement channel: it renders the current `ExperienceFact.claim` values that are reached directly from at least one rendered `supported` claim and whose `Confidence` is the maximum member under §10, with the fact ID, supporting claim IDs, and the fact's `source_log_ids` visible.
- Counterevidence renders inline in its claim's block, never as a section of its own.

The report carries its provenance at the claim level, inline:

- Every rendered claim block opens its fields with the claim's own ID — the identity a counterevidence reader, a §13.12 companion consumer, and Strongly Supported Facts' supporting-claim lists all join on.
- Every rendered claim block ends with one renderer-owned sources line built from the claim's stored `source_fact_ids`, the ID list ascending by UTF-8 bytes.
- That sources line contains typed IDs and fixed renderer-owned labels only, never explanatory factual prose.
- That sources line is never empty for a rendered claim, because §16.1 bars a chainless claim from export.
- The record-level closure — facts to evidence items to retained raw logs — is not repeated in the report: the §13.12 `evidence_map.json` companion is the complete machine-checkable typed link closure, while the report renders the claim-level trail for verification by reading.
- No section dumps the four link classes as standalone ID lists.

The tone should be:

```text
clear
specific
non-flattering
non-punitive
evidence-aware
```

Ordering and emptiness:

- Fixed headings always render in the order shown above.
- Within a primary claim section, claims sort by `SelfClaim.id` ascending in UTF-8 byte order.
- Strongly Supported Facts sort by `ExperienceFact.id`.
- Typed unknowns and contradictions sort by their own IDs.
- Within one claim block, counterevidence entries sort by (`source_ref_type`, `source_ref_id`) UTF-8 bytes.
- In the two mixed sections — Unknowns and Open Questions, and Contradictions — the status-selected primary `SelfClaim` rows render first in claim-ID order, followed respectively by referenced `GapQuestion` or `Contradiction` rows in their own ID order; the classes never interleave.
- A section with no selected row renders its heading and no filler sentence, placeholder, synthetic summary, or inferred transition.
- `Snapshot created` comes only from the persisted snapshot and never from export wall-clock time.
- These rules plus §13.12's JSON ordering make repeated rendering of the same coherent snapshot byte-identical.

**Markdown escaping.**

- Every nonliteral value inserted into Markdown, other than a validated source-voice excerpt, uses one deterministic escaping function.
- The function normalizes line endings to LF and Unicode to NFC, then applies the closed positional rule below to each logical line.
- Fixed title/heading labels and Markdown syntax are renderer-owned structural literals emitted exactly as specified and do not pass through this function.
- The rule is total — every code point has exactly one defined output — and positional only on a character's own line offset, so equal validated values always produce equal bytes and repeated rendering stays byte-identical.
- The rule escapes only what can change block structure or open an inline construct in the position where it is emitted.

  The reason is that `report.md` is itself a product surface: the owner reads the canonical file directly in an editor or terminal, so escaping punctuation that cannot change structure there — most visibly the separators inside ISO 8601 instants and typed IDs — degrades the mirror as plain text and additionally leaks backslashes in viewers that unescape a narrower set than CommonMark.
- The rule is defined over ASCII code points only and therefore does not vary with the Unicode version in use.

Its three groups are:

- **Character references.** A tab is `&#9;`, `<` is `&lt;`, `&` is `&amp;`, and every space in a leading or trailing run on a logical line is `&#32;`.

  These are the code points a backslash escape cannot make safe in every renderer: `<` and `&` are the only characters that could otherwise open raw HTML or an entity — so a downstream HTML view inherits no injection surface from this projection — while boundary spaces would otherwise add or remove indentation and hard breaks. The reference form renders as the original character under CommonMark and under stricter viewers alike.
- **Escaped at every position.** `\`, `` ` ``, `*`, `[`, `]`, and `~`. `_` is escaped except when it stands between two ASCII alphanumerics, where CommonMark's intraword rule already makes it inert, so a typed ID such as `fact_0001` renders unescaped.

  The set covers CommonMark inline constructs plus the widely deployed strikethrough extension, since the canonical file is read in unspecified viewers.
- **Escaped at the line-leading position** — the first character of every logical line whose line has no leading space run, which is the only offset where a block construct is recognized: `-`, `+`, `#`, `>`, `=`, `|`, and `:`. When such a line begins with one to nine ASCII digits followed by `.` or `)` and then a space or the line end, that delimiter is escaped.

  Together with the always-escaped set this closes every block opener: list markers, ATX and setext headings, block quotes, thematic breaks, fenced code, HTML blocks, link reference definitions, and table delimiter rows.

Closing rules for the Markdown member:

- No other code point is escaped.
- The line-leading group applies to a value's first logical line as well, so no call site can place a value in a block-opening position without its escapes.
- Embedded generated line breaks are joined with a service-authored Markdown hard break and the continuation indentation required to keep the value in its current block or list item.
- Generated and structural segments use UTF-8 and LF, and the complete file ends in exactly one LF.
- A validated source-voice excerpt is instead isolated in its own deterministic fenced block: the fence is the shortest backtick run of at least three code points longer than every backtick run in the excerpt, and the interior source bytes — including its original newline bytes — are emitted unchanged.
- The source interior is the sole exception to whole-member LF normalization and generated-voice NFC normalization.
- Structural escaping or fencing never supplies factual words.
- Missing escape containment, a value that changes section/block structure, or a source excerpt whose unescaped value no longer validates byte-for-byte under §16.12 fails rendering closed.

**The HTML member.**

- `report.html` renders that same document — title, header lines, fixed headings in their fixed order, the same selected rows in the same order, and the same values — through one closed set of renderer-owned structural elements, and it is self-contained by construction.
- The whole document is one file whose only styling is an inline stylesheet. The renderer emits none of the following:
  - Script.
  - Event-handler or inline `style` attribute.
  - Form.
  - Framed or embedded object.
  - Image, font, or stylesheet reference.
  - Absolute or relative URL of any kind.

  Opening the file therefore performs no network request and reads no second file.
- A `Content-Security-Policy` `meta` element states that boundary to the browser as defense in depth — `default-src 'none'`, the inline stylesheet admitted only by its own SHA-256 hash, `base-uri 'none'`, `form-action 'none'`.
- The guarantee is nonetheless structural: the policy is declared because §30 serves these same bytes, and removing it would still leave no external reference to make.
- The document declares UTF-8 and, under §16.13, English.
- Nothing outside the rendered snapshot enters the file: no export wall-clock value, no nonce, no workspace path, no host name, and no snapshot-derived value in the document head.
- Styling is presentation only — it never adds, removes, reorders, or substitutes content relative to `report.md`.
- Because the file carries no behavior, no claim, status, uncertainty, contradiction, gap, or counterevidence row can be hidden behind an interaction.

**HTML escaping.**

- Every nonliteral value inserted into HTML uses one deterministic escaping function, applied to the same LF- and NFC-normalized value the Markdown member escapes and never to already-escaped Markdown bytes.
- The function maps `&` to `&amp;`, `<` to `&lt;`, `>` to `&gt;`, `"` to `&quot;`, and `'` to `&#39;`, leaves every other code point unchanged, and emits an embedded generated line break as the renderer's structural break element.
- The rule is total and independent of position, so a value can never open, close, or alter an element or attribute wherever it is emitted, and equal validated values always produce equal bytes.
- Renderer-owned structural literals — the doctype, element and attribute names, attribute values, the stylesheet, and the fixed labels — are emitted exactly as specified and do not pass through this function.
- A value that reaches an attribute is escaped by the same function, so no value can terminate one.
- A validated source-voice excerpt renders inside a preformatted element under that same escaping, without newline or Unicode normalization, so its characters survive §16.12's byte-for-byte rule while never becoming markup.
- `report.html` uses UTF-8 and LF and ends in exactly one LF.
- Its bytes are a function of the same render inputs as `report.md`, so repeated rendering of the same coherent snapshot stays byte-identical and §13.14's member hash pins it.

Every rendered `SelfClaim` keeps its §16.11 status visible. The Summary renders the snapshot's required `narrative_summary` claim, so it is governed by the same status gate. `partially_supported` and `inferred_but_acceptable` content must not appear under Strongly Supported Facts. `needs_clarification` renders as uncertainty or a question. `contradicted` renders with its counterevidence inline in its own block, beside — never merged with — any coexisting detection row.

**Rendering `OccurredAt`.**

- Every rendered `OccurredAt` preserves its stated precision under §11.1 and never renders a date or time narrower than that precision supports.
- A calendar-aligned value normalized from a named period may render as its ISO week, month, quarter, or year label.
- A legal non-aligned value retains its original anchor and precision: the renderer must not re-align or relabel it as a named calendar period, and any displayed anchor is explicitly labeled representational rather than an exact occurrence date.
- `date_range` and `approximate_range` remain visibly ranges, and an approximate range remains visibly approximate.
- The maximum-uncertainty intervals in §16.7 are comparison semantics, not permission to render a narrower date.

**Rendering an open-ended range.**

- An open-ended range (§11.1) renders visibly open through one deterministic form built from three parts: its stored `start`, an explicit open-period label, and the attesting record's `recorded_at` as an as-of anchor — for a derived row, the governing record's under §13.3 rule 10 — with the approximate flavor still visible for an `approximate_range`.
- Both instants render in one canonical ISO 8601 form derived from the validated value, preserving its §12 rule 3 offset and never shifting it to another zone.
- That form is a function of the instant and its offset, not of a stored value's byte spelling, so a placement renders identically whether it arrives from storage or from a §11 model and equal validated values always produce equal bytes.
- The renderer never supplies or infers an end bound.
- The renderer never renders present-tense continuation such as "to the present", "currently", or "still".
- The renderer never consults export wall-clock time, so an open-ended placement renders identically however long after its capture the snapshot is exported and repeated rendering stays byte-identical.
- The as-of anchor is what the record attests (§16.7), not an occurrence bound, and is labeled as such.

**Gaps, and Unknowns and Open Questions.**

- The Gaps section renders status-bearing snapshot claims with `dimension = "gap"`.
- Unknowns and Open Questions is the one section that carries the snapshot's open questions, so the owner reads each question next to the identity §14.7 `gaps answer` needs and never joins two lists by hand.
- Unknowns and Open Questions renders no free-form snapshot prose: after its status-selected claims, for every ID in the snapshot's `gap_question_ids` — complete and unanswered as of synthesis — it renders one block presenting the referenced `GapQuestion.question` first, then the gap ID and the `target_*`, `reason`, and `priority` values as missing-information context, in gap-ID order.
- The unanswered blocks are the report's **open-question set** — the selection §30's gap handoff names.
- A question answered after synthesis renders its block with an explicit answered-since-synthesis marker and leaves the open-question set; its answer reaches the model only through extraction and the next generation.
- Missing, duplicate, or superseded IDs fail before rendering under §12 rule 10; a post-synthesis answer is visible state, never a rendering or export failure.
- These references present uncertainty under §13.6's unknown-reference boundary; question prose renders only through the referenced row.

Recurring Patterns and Interests carries the claims whose dimension names an orientation rather than a capability or a limit — `domain_interest`, `working_style`, `trajectory`, or `identity_hypothesis` — when rules 1–4 have not placed the claim first. It may render pattern-derived language only through a current `SelfClaim` referenced by the snapshot, with that claim's status and source mapping intact.

**Contradictions.**

- The Contradictions section renders every contradiction referenced by the selected current snapshot, including its title, description, and both typed source references.
- Each contradiction row carries the fixed renderer-owned origin label "unadjudicated detector output".
- The label states §13.4's lifecycle rule in presentation: a detection is Stage 4's reading of its inputs, no Stage 7 verdict retires or adjudicates it, and it may legitimately coexist with claim-level counterevidence that disputes what it describes — the reader weighs both, and the evidence-driven remedy runs through re-extraction and the next Stage 4 generation.
- Superseded contradictions appear only with historical snapshot inspection, never through current export.

**Counterevidence.**

- Counterevidence renders inside the claim block it annotates: every non-empty `SelfClaim.counterevidence` list — not only on `contradicted` claims — renders as nested entries under its claim in that claim's primary section, beside the claim ID, prose, and §16.11 status it qualifies, so the reader never joins an annotation to its claim across sections by hand.
- The entries are labeled as verifier-grounded contrary-evidence annotations.
- Each entry renders its `statement` with its typed (`source_ref_type`, `source_ref_id`) grounding reference so the reader can navigate from the annotation to the persisted source.
- Rendering re-validates resolvability like every typed reference, while closure membership and duplicate rejection were already enforced at the §13.7 write boundary.
- Counterevidence is not a separate claim, cannot improve a claim or snapshot status, and cannot guide Stage 10 as an independent prose channel.

All Exp2Res-authored report prose is generated voice under §16.12; a quoted source excerpt retains source voice only under §16.12's typed-reference byte-for-byte rule.

---
