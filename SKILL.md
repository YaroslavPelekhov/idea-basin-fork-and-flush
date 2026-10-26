---
name: idea-basin-fork-and-flush
description: Run budget-matched Fork-and-Flush optimization and build validated idea-basin maps from graded artifact trajectories. Use for long-horizon measurable research or engineering searches that need fresh-context branches, checkpoint selection, trajectory auditing, or paper-style t-STE/SOE basin analysis; do not use for subjective work without a reproducible evaluator.
---

# Idea-Basin Fork-and-Flush

Use the paper protocol as two separate, composable workflows:

1. **Search:** periodically fork the current workspace into independent workers with fresh chat contexts, evaluate every lineage with one frozen grader, keep the best verified checkpoint, and continue the winning session.
2. **Analysis:** turn graded artifacts into functional specifications, collect blind ordinal similarity judgments, fit and validate an idea map, and measure whether branches change basins.

The search does not require a basin map. The map is an auditable analysis of what the search explored. Never use map coordinates or cluster labels as substitutes for the task grader.

## Route the request

- For a new or continuing optimization run, read [paper protocol](references/paper-protocol.md) and [run records](references/run-records.md).
- For a basin map, read [idea-map protocol](references/idea-map-protocol.md) and [schemas](references/schemas.md).
- For Codex/Claude runtime mechanics, read [runtime adapters](references/runtimes.md).
- For a complete run, use all four references.

Resolve helper paths relative to this `SKILL.md`. Run `python3 scripts/fnf.py --help` for the deterministic CLI.

## Search invariants

- Freeze the evaluator, score direction, validity checks, seeds/repeats, model, reasoning effort, tools, task prompt, and total model-usage budget before search.
- Keep every worker in a separate writable workspace created from the same round snapshot.
- A flush means a genuinely new conversation with no inherited transcript or summary. Required user, system, task, and repository instructions still apply.
- Pass the workspace, not the parent reasoning. Keep controller logs and sibling results outside worker inputs.
- Default paper schedule: `K=4`; opening fan-out at 0%; later fan-outs at 33% and 66% of aggregate budget; each branch gets about 6% of total budget.
- Evaluate exact immutable checkpoints. Select the best verified checkpoint ever reached in a lineage, not its final mutable workspace.
- Count all branches, retries, and selection-affecting model calls. Never count parallel workers as one worker.
- Preserve the incumbent on ties. Invalid or unverified artifacts cannot win.
- Report the best result reached inside the budget separately from any episode overshoot.

Initialize and inspect a run with:

```sh
python3 scripts/fnf.py init-run --run-dir RUN --task-id TASK --budget 1000000 \
  --unit tokens --direction max --evaluator "COMMAND"
python3 scripts/fnf.py status --run-dir RUN
```

The CLI produces schedules, contracts, append-only event records, selection manifests, sampling manifests, judgment batches, embeddings, validation metrics, and an offline dashboard. `run-prompt-batch` can execute specification or judgment prompts through any explicit stdin/stdout model wrapper, with bounded parallelism and resumable JSONL output. It deliberately does not pretend to control a host feature that the current Codex/Claude runtime does not expose. The agent performs session creation and resumption through the available host tools and records those actions.

## Idea-map invariants

- Use exact graded artifacts; deduplicate by content hash; sample at most 51 evenly spaced first-occurrence snapshots per run.
- Rewrite each artifact into the same task-specific functional schema. Preserve mechanisms, data structures, control flow, quantitative choices, and explicitly absent techniques; remove names, formatting, history, and score.
- Similarity judges never receive score, chronology, run identity, or file names. Balance within-run and cross-run comparisons.
- Hold out 20% of ordinal constraints per anchor before fitting. Fit two-dimensional t-STE with eight restarts and retain the highest training agreement.
- Fit SOE to the same constraints as a robustness check. Report held-out agreement for both; chance is 0.5.
- Fit basins on baseline artifacts only, then assign intervention artifacts to those fixed centroids. Do not infer strong basin structure when held-out agreement, clustering stability, or outcome separation is weak.
- Report map uncertainty and coordinate non-identifiability. Distances are interpreted only within one fitted map and normalized by median consecutive baseline step where appropriate.

## Required result

Return:

- baseline and best-in-budget score with exact evidence;
- aggregate measured/estimated compute and deviations from the schedule;
- complete lineage/checkpoint manifest, including discarded branches;
- if mapping was requested: sampled-artifact manifest, specification and judgment coverage, t-STE/SOE held-out agreement, selected basin count, stability, score separation, transition matrix, normalized branch jumps, and the generated dashboard;
- a clear distinction between measurements, estimates, and planned work.

Do not claim the paper's gains for a new task. The method preserves a chance to explore different approaches; it does not guarantee improvement or meaningful basins.
