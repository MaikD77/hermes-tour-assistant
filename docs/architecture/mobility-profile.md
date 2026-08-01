# Mobility Profile architecture

The dependency direction is strictly `LocationObservation → Movement →
Place/Stay → Mobility Profile → PersonalContextFact`. The final layer reads
lower-layer contracts and never mutates them. It has no delivery or provider
dependency and runs only in shadow mode.

Place aggregation records counts, distinct/observed days, visits/week, active
day share, total/mean/median dwell, bounded local arrival/departure/dwell
buckets, weekday/weekend distribution, overnight share, quality and recency.
Circular quantiles unwrap clock values at their largest gap. Overnight is real
elapsed overlap with each configured local night interval, handling DST.

Confidence is the bounded weighted sum of sample sufficiency (25%), distinct
days (20%), quality (20%), temporal/mode consistency (15%), span (10%) and
recency (10%), minus up to 15% for outliers. Status applies configured
sample/day/confidence and stale/revoke thresholds.

Transitions require plausible departure, covering gap-free segments, and an
arrival within the configured maximum. Aggregates retain only direction,
median/IQR duration, mode, weekdays, confidence and status—never route geometry.
Persistence uses the common locked, atomic, no-symlink repository. Export omits
samples and deduplication IDs; Forget removes both transition directions.

Facts are invisible until both their candidate sample threshold and candidate
confidence threshold are reached. Confirmation additionally requires its own
sample, distinct-day and confidence thresholds. Transitions use dedicated
candidate and confirmation sample/day settings rather than Place visit counts.

Retention is time-based and inclusive at `computed_at - retention_days`.
Coordinate-free retained visit evidence and transition samples older than that
instant are discarded, after which every aggregate and fact is reproduced only
from remaining evidence. Therefore expired totals do not accumulate forever;
if no sufficient retained evidence remains, the fact/pattern disappears.

`profile forget-place` affects only profile state: it removes retained visit
evidence, deduplication IDs, statistics, facts, and transitions in both
directions. The lower Place state remains authoritative, so a later profile
rebuild can intentionally learn the place again. Permanent removal across both
layers requires `place forget` before rebuilding the profile.
