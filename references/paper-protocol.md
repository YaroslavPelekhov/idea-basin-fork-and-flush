# Paper protocol and extensions

## Paper-faithful search mode

Let a state be `(workspace, conversation)` and a lineage be a sequence of states. A deterministic grader maps a workspace to a task score. The lineage value is the best valid score it reached, and the run value is the best lineage value reached within aggregate budget `B`.

The fixed schedule is:

1. At 0% of `B`, launch four fresh-context workers from the same opening workspace. Give each `0.06B`.
2. Select the branch with the best verified lineage value. Continue its existing conversation and workspace until aggregate spend reaches 33% of `B`.
3. Snapshot that workspace. At 33%, launch four fresh-context workers from identical copies. Give each `0.06B`, select the best, and continue it to 66%.
4. Repeat at 66%, then continue the winner until `B`.
5. Return the best verified artifact produced by any lineage within `B`, including pruned lineages.

For integer budgets, round branch shares down and give the remainder to serial exploitation. Budget is aggregate model usage, not elapsed time. Keep task-training/evaluator compute separately if it matters.

## Matched baselines

For a scientific comparison, use the same task, model, reasoning effort, tools, prompt, evaluator, and aggregate budget:

- **single-run:** one uninterrupted lineage receives `B`;
- **best-of-4:** four fresh independent lineages receive `B/4`; keep their best result;
- **flush-only:** one fresh-context continuation at each scheduled intervention;
- **fork-only:** four continuations inherit both workspace and conversation state when the runtime can reproduce this condition;
- **fork-and-flush:** four branches inherit workspace but receive fresh conversations.

Repeat complete conditions when making statistical claims. Preserve per-run final score, maximum, mean, standard deviation, and task-normalized aggregates.

## Extensions beyond the paper

These are optional and must be labelled as extensions:

- automatic `k` selection using silhouette plus bootstrap stability;
- confidence intervals for basin score separation and transition rates;
- balanced triplet generation with deterministic hashes;
- complete append-only provenance and content hashes;
- static HTML dashboard and machine-readable CSV/JSON outputs;
- policy comparison through replay, provided all policies see the same historical evidence.

Do not silently replace the fixed schedule with plateau detection. The paper found fixed triggers more reliable because micro-improvements and variable episode cost make plateau thresholds unstable.

## Attribution

The search and map protocol follows *Fork-and-Flush: Escaping Idea Basins in Autoresearch Agents* (Cai et al., arXiv:2610.07447v1, 2026), especially Sections 2–6 and Appendices D–I. This skill is an independent implementation, not the authors' official harness.
