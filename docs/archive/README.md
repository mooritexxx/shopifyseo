# Archive

Completed plans and design specs for features that have **shipped**. Kept for decision history — *why* something was built a given way — not as a description of current behaviour.

**These documents are not maintained.** They describe intent at the time of writing and have drifted from the code. For current behaviour see [TECHNICAL_DOC.md](../../TECHNICAL_DOC.md); for the current schema see [seo-database-blueprint.md](../seo-database-blueprint.md).

## Contents

| Document | Written | What it records |
|---|---|---|
| [overview-dashboard-plan.md](overview-dashboard-plan.md) | Apr 2026 | Overview dashboard redesign. All phases complete. Source of the locked decisions to drop the attention queue, drop `indexing_candidates`, and use matched-day period comparison. |
| [specs/](specs) | Mar 27–29, 2026 | Nine design docs covering keyword research, keyword clustering, GSC cross-referencing, cluster matching and context, cluster detail view, aggregate cluster coverage, and GSC dimensional analytics. All shipped under the `keywords` and `clusters` routers. |

## Before reusing anything here

Check it against the code first. Known drift as of Aug 2026:

- The overview plan's "Current state" table reflects April, not today.
- Specs predate the performance pass recorded in [CHANGELOG.md](../../CHANGELOG.md); several query paths they describe have since been rewritten around the expression indexes in TECHNICAL_DOC's **Performance Invariants**.
