# Data schemas

## Artifact input (`artifacts.jsonl`)

Required fields:

- `artifact_id`: globally unique stable identifier;
- `run_id`: trajectory identifier;
- `sequence`: integer order of first graded occurrence;
- `path`: artifact file or directory;
- `score`: finite task score;
- `condition`: for example `single-run`, `best-of-4`, `fork-and-flush`;
- `baseline`: boolean; only baseline artifacts define basin centroids.

Optional: `lineage`, `parent_id`, `round`, `usage`, `selected`, `fork_point_id`.

For pooled campaigns with different score offsets, provide `analysis_score` as a predeclared within-campaign normalization (for example a percentile). The map still retains the raw task `score`, but basin-quality summaries use `analysis_score` to avoid confusing campaign identity with basin quality.

## Sample manifest

The collector computes a content hash, removes exact duplicates globally, preserves the first occurrence, and chooses at most 51 evenly spaced records from every run. Directory hashes include sorted relative paths and file bytes.

## Specifications (`specifications.jsonl`)

Each row has `artifact_id`, `specification`, `prompt_version`, and optional `model`/`usage`. The common task-specific schema must cover:

1. objective;
2. overall strategy;
3. input and instance handling;
4. representation;
5. core search or inference mechanism;
6. task-specific candidate selection;
7. portfolio, randomization, and tie-breaking;
8. data structures;
9. control flow and compute limits;
10. exhaustive parameters;
11. techniques explicitly not implemented.

## Judgment query and result

A query contains `query_id`, artifact IDs `a`, `b`, `c`, their specifications, and a blinded prompt. The result repeats `query_id` and returns a strict permutation of `AB`, `AC`, `BC`, ordered from most similar to least similar, plus a reason. Scores, chronology, run identity, and source names are excluded.

Every strict ranking expands to three ordinal constraints. For order `AB < AC < BC`:

- anchor A: B is closer than C;
- anchor B: A is closer than C;
- anchor C: A is closer than B.

Existing paper-style datasets can instead provide constraints directly as JSONL rows with `anchor`, `positive`, `negative`, and `split`, or as pre-split `N x 3` NumPy index arrays. This avoids manufacturing a complete pair ranking when only an anchor comparison was originally collected.

## Map outputs

- `coordinates.csv`: artifact metadata, t-STE/SOE coordinates, basin, score;
- `constraints.jsonl`: train/held-out ordinal constraints;
- `metrics.json`: agreement, selected `k`, silhouette, stability, score separation, transitions, and normalized jumps;
- `dashboard.html`: offline interactive-free portable report with embedded SVG plots and tables.
