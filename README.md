# Idea-Basin Fork-and-Flush

A portable skill for running budget-matched Fork-and-Flush search in Codex or Claude Code and, when useful, building a validated map of the explored solution space.

The package separates two workflows:

1. **Search:** periodically fork the current best checkpoint into fresh independent sessions, evaluate every branch with one frozen evaluator, and continue from the best valid checkpoint reached within the shared budget.
2. **Analysis:** rewrite graded artifacts into a common functional representation, collect blinded ordinal similarity judgments, fit a two-dimensional map, validate it out of sample, and measure transitions between basins.

The search does not require a map. A basin label describes similarity between mechanisms; it is never used as a substitute for the task metric.

## What is included

- deterministic budget planning for the paper-style `4 × 6%` fork schedule;
- checkpoint validation and best-ever selection for maximizing or minimizing metrics;
- append-only run records and resumable prompt batches;
- content deduplication and trajectory sampling;
- blinded triplet generation and judgment validation;
- t-STE and SOE map fitting with held-out agreement;
- basin selection, stability and outcome-separation diagnostics;
- offline HTML dashboard generation;
- installers for Codex and Claude Code;
- unit, integration and synthetic end-to-end tests.

## Requirements

- Python 3.10+
- NumPy
- SciPy
- scikit-learn
- pytest for the test suite

Install dependencies:

```bash
python3 -m pip install -r requirements.txt pytest
```

## Install as a skill

Install for both runtimes:

```bash
python3 scripts/install.py --target both
```

Default destinations:

- Codex: `~/.agents/skills/idea-basin-fork-and-flush`
- Claude Code: `~/.claude/skills/idea-basin-fork-and-flush`

Custom destinations are supported:

```bash
python3 scripts/install.py --target codex --codex-dir /path/to/codex/skills
python3 scripts/install.py --target claude --claude-dir /path/to/claude/skills
```

An existing modified installation is not overwritten unless `--replace` is given. Replacement first creates a timestamped backup outside the skill directory.

## Quick start

Create a frozen run contract and exact compute schedule:

```bash
python3 scripts/fnf.py init-run \
  --run-dir runs/example \
  --task-id circle-packing-9 \
  --budget 1000000 \
  --unit tokens \
  --direction max \
  --evaluator "python3 evaluate.py"
```

Inspect progress:

```bash
python3 scripts/fnf.py status --run-dir runs/example
```

List all commands:

```bash
python3 scripts/fnf.py --help
```

The skill instructions in [`SKILL.md`](SKILL.md) define the complete workflow. Exact schemas and experimental invariants are documented in [`references/`](references/).

## Cross-task behavior

The controller does not assume a particular problem, model, or metric. The test suite covers:

| Task fixture | Objective | Budget unit |
|---|---:|---|
| Nine-circle packing | maximize | tokens |
| Route length | minimize | cost in micro-USD |
| DCLM validation | maximize | credits |

In every fixture, schedule accounting is exact, invalid checkpoints cannot win, and checkpoints after the declared budget are excluded.

## Verification

Run all tests without inheriting a parent repository's pytest configuration:

```bash
python3 -m pytest -q -c /dev/null --confcutdir=tests tests
```

Expected result for this version: `17 passed`.

Validate the skill package with the Codex skill validator:

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py .
```

## Reproducibility rules

- Freeze the evaluator, score direction, seeds, runtime, model settings and total aggregate budget before search.
- Use a separate writable workspace and fresh conversation for each exploratory branch.
- Count every branch and selection-affecting model call.
- Evaluate immutable checkpoints and keep the incumbent on exact ties.
- Keep measured values, estimates and planned work distinct.
- Validate map geometry on held-out triplets before interpreting clusters.

## Scope

This repository implements the control, evidence and analysis layer. Codex or Claude Code remains responsible for creating fresh sessions and continuing the selected session through the host capabilities available in that runtime. The method does not guarantee improvement and does not assume that every task has meaningful basins.

