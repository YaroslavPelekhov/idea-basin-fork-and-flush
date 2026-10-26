# Run contract and records

The run directory is coordinator-owned and must not be inherited by workers:

```text
run/
  contract.json
  schedule.json
  events.jsonl
  checkpoints.jsonl
  selections/
  evidence/
  maps/
```

`contract.json` freezes task identity, direction, evaluator command/version, validity checks, seeds or repeat aggregation, model/reasoning setting, editable paths, snapshot, budget/unit, usage source, concurrency, and schedule.

Each event contains a unique ID, UTC time, event type, round, lineage, checkpoint if applicable, cumulative aggregate usage, usage kind (`measured`, `estimated`, or `mixed`), and evidence. Record failed and cancelled episodes too.

Each checkpoint contains:

```json
{
  "schema_version": 1,
  "checkpoint": "round-1-worker-2-c004",
  "lineage": "round-1-worker-2",
  "parent": "leader-c017",
  "round": 1,
  "score": 12.5,
  "valid": true,
  "artifact": "/absolute/path/checkpoint.tar",
  "artifact_sha256": "...",
  "evidence": "/absolute/path/evaluation.json",
  "cumulative_usage": 43100,
  "usage_kind": "measured",
  "run_id": "baseline-3",
  "condition": "fork-and-flush",
  "sequence": 17
}
```

The selector validates structure and can optionally require artifact/evidence files and matching SHA-256. It filters by the budget boundary before ranking. Invalid records remain in provenance but cannot win. A tie retains the incumbent and then uses stable lineage/checkpoint identifiers.

The evaluator evidence should contain its command/version, environment, inputs, seeds, raw repeat measurements, aggregate, validity checks, exit code, and exact artifact digest. Never allow a worker to edit the frozen evaluator or its held-out inputs.
