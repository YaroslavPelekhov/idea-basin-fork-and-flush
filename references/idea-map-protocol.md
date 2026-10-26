# Validated idea-map protocol

## 1. Collect and sample

Collect every exact graded submission, not arbitrary working-tree snapshots. Hash artifact contents, remove exact duplicates, and keep the first occurrence. Within each run, choose at most 51 indices evenly across ordered first occurrences. Save the manifest before any LLM processing.

## 2. Canonicalize function, not prose

Use one specification prompt for the entire task. Give the model the task statement, mechanisms that distinguish plausible solutions, the eleven-section schema, and one artifact. Require reimplementation-level functional detail while excluding naming, formatting, comments, authorship, chronology, and score. Use `make-spec-prompts` and save responses as JSONL.

For large batches, connect any model provider through a user-supplied command that reads one prompt from stdin and writes one response to stdout:

```sh
python3 scripts/fnf.py run-prompt-batch prompts.jsonl --mode specification \
  --command "MODEL_WRAPPER --model FIXED_MODEL" --jobs 8 --output specifications.jsonl
```

The runner never constructs a shell command, resumes from existing IDs, validates judgment JSON, and writes failures separately.

## 3. Collect blind ordinal judgments

Generate balanced triples across runs and conditions. The judge sees only task context and three canonical specifications. It ranks all three pairs by implemented-mechanism similarity. Do not expose scores or provenance. Use deterministic seeds and query IDs so interrupted batches resume without duplication.

The paper used 6,375 judgments per mapped task, except 5,610 for one task. Smaller diagnostic maps are allowed but must report their judgment count and must not claim paper-level fidelity.

## 4. Split before fitting

Expand rankings to anchor-positive-negative constraints. Deterministically hold out one fifth per anchor. Never select restarts, dimensions, or cluster count using held-out agreement.

## 5. Fit and validate

Fit two-dimensional t-STE with eight seeded restarts; choose the restart with the highest train agreement. Evaluate once on held-out constraints. Refit the same split with SOE and report both. Chance is 0.5. Also report the run-membership heuristic when multiple runs exist.

## 6. Identify basins

Fit k-means only on baseline coordinates. In paper-reproduction mode supply the intended `k`. In exploratory mode, choose `k` from a bounded range using training-only t-STE/SOE silhouette, bootstrap stability, agreement of the two independent clusterings, and a penalty for tiny or needlessly numerous clusters; label this as an extension. Assign intervention artifacts to the nearest fixed baseline centroid.

Do not claim distinct basins solely from a visually separated 2-D plot. Require acceptable held-out ordinal agreement and report:

- cluster stability across resamples and both embedding methods;
- score distributions and effect sizes by basin;
- contingency between run and basin;
- within-run versus cross-run distances;
- transitions and dwell time;
- first post-flush displacement normalized by the median consecutive baseline step.

## 7. Interpret conservatively

The embedding is identifiable only up to rotation, reflection, translation, and scale. Compare cluster membership and normalized distances, not raw coordinates across independently fitted maps. Weak maps are valid negative results.
