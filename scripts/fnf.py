#!/usr/bin/env python3
"""Deterministic controller and idea-map pipeline for Idea-Basin Fork-and-Flush."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import html
import itertools
import json
import math
from pathlib import Path
import random
import shlex
import subprocess
import sys
from typing import Any, Iterable


SCHEMA_VERSION = 1
PAIR_ANCHORS = {
    "A": ("AB", "AC", "B", "C"),
    "B": ("AB", "BC", "A", "C"),
    "C": ("AC", "BC", "A", "B"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{number}: expected JSON object")
            rows.append(value)
    return rows


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value.strip()


def plan_schedule(
    budget: int,
    branches: int = 4,
    branch_fraction: float = 0.06,
    triggers: tuple[float, ...] = (0.0, 0.33, 0.66),
    unit: str = "credits",
) -> dict[str, Any]:
    if type(budget) is not int or budget < 1:
        raise ValueError("budget must be a positive integer")
    if type(branches) is not int or branches < 1:
        raise ValueError("branches must be a positive integer")
    if unit not in {"credits", "tokens", "cost-microusd"}:
        raise ValueError("unit must be credits, tokens, or cost-microusd")
    if not 0 < branch_fraction < 1:
        raise ValueError("branch_fraction must be between zero and one")
    if not triggers or triggers[0] != 0 or any(a >= b for a, b in zip(triggers, triggers[1:])) or triggers[-1] >= 1:
        raise ValueError("triggers must start at 0 and increase below 1")
    per_branch = math.floor(budget * branch_fraction)
    if per_branch < 1:
        raise ValueError("budget is too small for the branch allocation")
    bounds = [math.floor(budget * trigger) for trigger in triggers] + [budget]
    phases = []
    for round_id, (start, end) in enumerate(zip(bounds, bounds[1:])):
        explore = branches * per_branch
        if start + explore >= end:
            raise ValueError(f"round {round_id}: exploration leaves no exploitation budget")
        phases.extend(
            [
                {
                    "kind": "explore",
                    "round": round_id,
                    "start": start,
                    "end": start + explore,
                    "workers": branches,
                    "per_worker": per_branch,
                    "allocation": explore,
                    "fresh_context": True,
                },
                {
                    "kind": "exploit",
                    "round": round_id,
                    "start": start + explore,
                    "end": end,
                    "workers": 1,
                    "per_worker": end - start - explore,
                    "allocation": end - start - explore,
                    "fresh_context": False,
                },
            ]
        )
    assert sum(phase["allocation"] for phase in phases) == budget
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": "paper-fixed-schedule",
        "total_budget": budget,
        "budget_unit": unit,
        "branches": branches,
        "branch_fraction": branch_fraction,
        "triggers": list(triggers),
        "phases": phases,
    }


def init_run(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = args.run_dir.resolve()
    if run_dir.exists() and any(run_dir.iterdir()):
        raise ValueError(f"run directory is not empty: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    schedule = plan_schedule(args.budget, args.branches, args.branch_fraction, tuple(args.triggers), args.unit)
    contract = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "task_id": nonempty(args.task_id, "task_id"),
        "direction": args.direction,
        "evaluator": nonempty(args.evaluator, "evaluator"),
        "evaluator_version": args.evaluator_version,
        "budget": args.budget,
        "budget_unit": args.unit,
        "usage_source": args.usage_source,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "snapshot": args.snapshot,
        "schedule": "schedule.json",
        "paper_mode": True,
    }
    write_json(run_dir / "contract.json", contract)
    write_json(run_dir / "schedule.json", schedule)
    for directory in ("evidence", "selections", "maps"):
        (run_dir / directory).mkdir()
    (run_dir / "events.jsonl").touch()
    (run_dir / "checkpoints.jsonl").touch()
    return {"run_dir": str(run_dir), "contract": contract, "schedule": schedule}


def validate_checkpoint(record: dict[str, Any], require_files: bool = False) -> dict[str, Any]:
    nonempty(record.get("checkpoint"), "checkpoint")
    nonempty(record.get("lineage"), "lineage")
    if type(record.get("valid")) is not bool:
        raise ValueError("valid must be a boolean")
    if record["valid"]:
        finite_number(record.get("score"), "score")
        artifact = Path(nonempty(record.get("artifact"), "artifact"))
        evidence = Path(nonempty(record.get("evidence"), "evidence"))
        if require_files and (not artifact.exists() or not evidence.exists()):
            raise ValueError(f"missing artifact or evidence for {record['checkpoint']}")
        expected = record.get("artifact_sha256")
        if require_files and expected and hash_artifact(artifact) != expected:
            raise ValueError(f"artifact digest mismatch for {record['checkpoint']}")
    elif record.get("score") is not None:
        finite_number(record["score"], "score")
    if "cumulative_usage" in record:
        finite_number(record["cumulative_usage"], "cumulative_usage")
    return record


def select_checkpoint(
    records: Iterable[dict[str, Any]],
    direction: str,
    incumbent: str | None = None,
    budget: float | None = None,
    require_files: bool = False,
) -> dict[str, Any]:
    if direction not in {"min", "max"}:
        raise ValueError("direction must be min or max")
    seen: set[str] = set()
    accepted, rejected = [], []
    for raw in records:
        record = validate_checkpoint(dict(raw), require_files)
        checkpoint = record["checkpoint"]
        if checkpoint in seen:
            raise ValueError(f"duplicate checkpoint: {checkpoint}")
        seen.add(checkpoint)
        if budget is not None and finite_number(record.get("cumulative_usage"), "cumulative_usage") > budget:
            rejected.append({"checkpoint": checkpoint, "reason": "after_budget"})
        elif not record["valid"]:
            rejected.append({"checkpoint": checkpoint, "reason": "invalid"})
        else:
            accepted.append(record)
    if incumbent is not None and not any(row["checkpoint"] == incumbent for row in accepted):
        raise ValueError("incumbent is not a valid in-budget record")
    if not accepted:
        raise ValueError("no valid in-budget checkpoint")
    winner = min(
        accepted,
        key=lambda row: (
            -row["score"] if direction == "max" else row["score"],
            row["checkpoint"] != incumbent,
            row["lineage"],
            row["checkpoint"],
        ),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "direction": direction,
        "budget": budget,
        "incumbent": incumbent,
        "winner": winner,
        "valid_count": len(accepted),
        "rejected": rejected,
    }


def hash_artifact(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    elif path.is_dir():
        for item in sorted(p for p in path.rglob("*") if p.is_file()):
            digest.update(str(item.relative_to(path)).encode())
            digest.update(b"\0")
            digest.update(item.read_bytes())
            digest.update(b"\0")
    else:
        raise ValueError(f"artifact path does not exist: {path}")
    return digest.hexdigest()


def evenly_spaced_indices(size: int, limit: int) -> list[int]:
    if size <= limit:
        return list(range(size))
    if limit < 2:
        return [0]
    return sorted({round(i * (size - 1) / (limit - 1)) for i in range(limit)})


def sample_artifacts(rows: list[dict[str, Any]], limit: int = 51) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = {"artifact_id", "run_id", "sequence", "path", "score", "condition", "baseline"}
    ids, hashes, unique = set(), set(), []
    duplicate_count = 0
    for row in sorted(rows, key=lambda item: (str(item.get("run_id")), int(item.get("sequence", -1)))):
        missing = required - row.keys()
        if missing:
            raise ValueError(f"artifact row missing fields: {sorted(missing)}")
        artifact_id = nonempty(row["artifact_id"], "artifact_id")
        if artifact_id in ids:
            raise ValueError(f"duplicate artifact_id: {artifact_id}")
        ids.add(artifact_id)
        finite_number(row["score"], "score")
        if type(row["baseline"]) is not bool:
            raise ValueError("baseline must be a boolean")
        item = dict(row)
        item["path"] = str(Path(item["path"]).resolve())
        item["content_sha256"] = hash_artifact(Path(item["path"]))
        if item["content_sha256"] in hashes:
            duplicate_count += 1
            continue
        hashes.add(item["content_sha256"])
        unique.append(item)
    by_run: dict[str, list[dict[str, Any]]] = {}
    for row in unique:
        by_run.setdefault(row["run_id"], []).append(row)
    sampled = []
    for run_id in sorted(by_run):
        ordered = sorted(by_run[run_id], key=lambda item: int(item["sequence"]))
        sampled.extend(ordered[index] for index in evenly_spaced_indices(len(ordered), limit))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "input_count": len(rows),
        "unique_count": len(unique),
        "exact_duplicates_removed": duplicate_count,
        "sampled_count": len(sampled),
        "run_count": len(by_run),
        "max_per_run": limit,
    }
    return sampled, manifest


SPEC_SECTIONS = [
    "OBJECTIVE",
    "OVERALL STRATEGY",
    "INPUT AND INSTANCE HANDLING",
    "REPRESENTATION",
    "CORE SEARCH OR INFERENCE",
    "CANDIDATE SELECTION",
    "PORTFOLIO RANDOMIZATION AND TIE-BREAKING",
    "DATA STRUCTURES",
    "CONTROL FLOW AND COMPUTE LIMITS",
    "PARAMETERS",
    "NOT IMPLEMENTED",
]


def specification_prompt(task: str, mechanisms: str, artifact_text: str) -> str:
    headings = "\n".join(f"## {i}. {name}" for i, name in enumerate(SPEC_SECTIONS, 1))
    return f"""Rewrite the submitted artifact as a functional specification for this task:
{task}

Mechanisms that commonly distinguish approaches in this task:
{mechanisms}

The specification must let a competent practitioner reproduce materially the same behavior. Preserve algorithms, representations, data structures, control flow, all numerical and categorical choices, randomization, stopping rules, and absent techniques. Exclude identifiers, formatting, comments, prose style, authorship, chronology, score, and development history. Refer to components by role. Use exactly these headings, writing 'Not implemented.' when appropriate:

{headings}

Return the specification only.

=== ARTIFACT ===
{artifact_text}
"""


def make_spec_prompts(sampled: list[dict[str, Any]], task: str, mechanisms: str, max_bytes: int) -> list[dict[str, Any]]:
    prompts = []
    for row in sampled:
        path = Path(row["path"])
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
        else:
            parts = []
            used = 0
            for item in sorted(p for p in path.rglob("*") if p.is_file()):
                header = f"\n=== FILE {item.relative_to(path)} ===\n"
                text = item.read_text(encoding="utf-8", errors="replace")
                take = max(0, max_bytes - used - len(header))
                if take == 0:
                    break
                parts.append(header + text[:take])
                used += len(parts[-1])
            content = "".join(parts)
        prompts.append(
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_id": row["artifact_id"],
                "prompt_version": "functional-spec-v1",
                "prompt": specification_prompt(task, mechanisms, content),
            }
        )
    return prompts


def triplet_prompt(task: str, mechanisms: str, a: str, b: str, c: str) -> str:
    return f"""For the same task described below, rank the three pairs AB, AC, and BC from most similar to least similar in implemented strategy.

Task: {task}
Relevant mechanisms: {mechanisms}

Judge functional mechanisms, representations, control flow, and quantitative choices. Ignore wording and formatting. Score, chronology, run identity, and file names are intentionally absent. Return strict JSON with keys 'order' (a permutation of ['AB','AC','BC']) and 'reason'.

=== A ===
{a}
=== B ===
{b}
=== C ===
{c}
"""


def make_triplet_queries(
    sampled: list[dict[str, Any]],
    specifications: list[dict[str, Any]],
    count: int,
    seed: int,
    task: str,
    mechanisms: str,
) -> list[dict[str, Any]]:
    specs = {row["artifact_id"]: nonempty(row.get("specification"), "specification") for row in specifications}
    items = [row for row in sampled if row["artifact_id"] in specs]
    if len(items) < 3:
        raise ValueError("at least three specified artifacts are required")
    combinations = list(itertools.combinations(range(len(items)), 3))
    rng = random.Random(seed)
    rng.shuffle(combinations)
    same, mixed = [], []
    for indices in combinations:
        rows = [items[i] for i in indices]
        target = same if len({row["run_id"] for row in rows}) == 1 else mixed
        target.append(indices)
    chosen = []
    for index in range(min(count, len(combinations))):
        bucket = same if index % 2 == 0 and same else mixed
        if not bucket:
            bucket = mixed if mixed else same
        chosen.append(bucket.pop())
    queries = []
    for indices in chosen:
        rows = [items[i] for i in indices]
        ids = [row["artifact_id"] for row in rows]
        query_id = hashlib.sha256(("|".join(sorted(ids)) + f"|{seed}").encode()).hexdigest()[:20]
        queries.append(
            {
                "schema_version": SCHEMA_VERSION,
                "query_id": query_id,
                "a": ids[0],
                "b": ids[1],
                "c": ids[2],
                "prompt": triplet_prompt(task, mechanisms, specs[ids[0]], specs[ids[1]], specs[ids[2]]),
            }
        )
    return queries


def parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value


def run_prompt_batch(
    prompts: list[dict[str, Any]],
    output: Path,
    command: str,
    mode: str,
    jobs: int,
    timeout: float,
) -> dict[str, Any]:
    if mode not in {"specification", "judgment"}:
        raise ValueError("mode must be specification or judgment")
    if jobs < 1:
        raise ValueError("jobs must be positive")
    argv = shlex.split(command)
    if not argv:
        raise ValueError("command must not be empty")
    key = "artifact_id" if mode == "specification" else "query_id"
    completed = {row[key] for row in read_jsonl(output)} if output.exists() else set()
    pending = [row for row in prompts if row.get(key) not in completed]

    def execute(row):
        identity = nonempty(row.get(key), key)
        prompt = nonempty(row.get("prompt"), "prompt")
        result = subprocess.run(argv, input=prompt, text=True, capture_output=True, timeout=timeout, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"{identity}: model wrapper exited {result.returncode}: {result.stderr[-500:]}")
        if mode == "specification":
            response = result.stdout.strip()
            if not response:
                raise ValueError(f"{identity}: empty specification")
            return {"schema_version": SCHEMA_VERSION, key: identity, "specification": response, "prompt_version": row.get("prompt_version", "functional-spec-v1")}
        response = parse_json_response(result.stdout)
        order = response.get("order")
        if not isinstance(order, list) or len(order) != 3 or set(order) != {"AB", "AC", "BC"}:
            raise ValueError(f"{identity}: invalid pair ordering")
        return {"schema_version": SCHEMA_VERSION, key: identity, "order": order, "reason": str(response.get("reason", ""))}

    results, failures = [], []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {executor.submit(execute, row): row for row in pending}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append({key: futures[future].get(key), "error": str(exc)})
    for row in sorted(results, key=lambda item: str(item[key])):
        append_jsonl(output, row)
    if failures:
        emit_jsonl(output.with_suffix(output.suffix + ".errors.jsonl"), failures)
    return {"already_completed": len(completed), "completed": len(results), "failed": len(failures), "remaining": len(pending) - len(results)}


def expand_judgments(queries: list[dict[str, Any]], judgments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {row["query_id"]: row for row in queries}
    constraints = []
    seen = set()
    for judgment in judgments:
        query_id = nonempty(judgment.get("query_id"), "query_id")
        if query_id in seen:
            raise ValueError(f"duplicate judgment: {query_id}")
        seen.add(query_id)
        if query_id not in by_id:
            raise ValueError(f"unknown query_id: {query_id}")
        order = judgment.get("order")
        if not isinstance(order, list) or set(order) != {"AB", "AC", "BC"} or len(order) != 3:
            raise ValueError(f"{query_id}: order must be a permutation of AB, AC, BC")
        rank = {pair: index for index, pair in enumerate(order)}
        query = by_id[query_id]
        labels = {"A": query["a"], "B": query["b"], "C": query["c"]}
        for anchor, (left_pair, right_pair, left, right) in PAIR_ANCHORS.items():
            positive, negative = (left, right) if rank[left_pair] < rank[right_pair] else (right, left)
            constraints.append(
                {
                    "query_id": query_id,
                    "anchor": labels[anchor],
                    "positive": labels[positive],
                    "negative": labels[negative],
                }
            )
    return constraints


def split_constraints(constraints: list[dict[str, Any]], holdout: float = 0.2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not 0 < holdout < 1:
        raise ValueError("holdout must be between zero and one")
    by_anchor: dict[str, list[dict[str, Any]]] = {}
    for row in constraints:
        by_anchor.setdefault(row["anchor"], []).append(row)
    train, test = [], []
    for anchor, rows in sorted(by_anchor.items()):
        ordered = sorted(rows, key=lambda row: hashlib.sha256(
            f"{row['query_id']}|{row['anchor']}|{row['positive']}|{row['negative']}".encode()
        ).hexdigest())
        n_test = max(1, round(len(ordered) * holdout)) if len(ordered) > 1 else 0
        test.extend(ordered[:n_test])
        train.extend(ordered[n_test:])
    if not train or not test:
        raise ValueError("insufficient constraints for a nonempty train/holdout split")
    return train, test


def scientific_modules():
    try:
        import numpy as np
        from scipy.optimize import minimize
        from scipy.linalg import orthogonal_procrustes
        from sklearn.cluster import KMeans
        from sklearn.metrics import adjusted_rand_score, silhouette_score
    except ImportError as exc:
        raise RuntimeError("map fitting requires numpy, scipy, and scikit-learn") from exc
    return np, minimize, orthogonal_procrustes, KMeans, adjusted_rand_score, silhouette_score


def indexed_constraints(rows: list[dict[str, Any]], index: dict[str, int]):
    return [(index[row["anchor"]], index[row["positive"]], index[row["negative"]]) for row in rows]


def agreement(coords, constraints: list[tuple[int, int, int]]) -> float:
    if not constraints:
        return float("nan")
    good = 0.0
    for a, p, n in constraints:
        dp = float(((coords[a] - coords[p]) ** 2).sum())
        dn = float(((coords[a] - coords[n]) ** 2).sum())
        good += 1.0 if dp < dn else 0.5 if dp == dn else 0.0
    return good / len(constraints)


def fit_embedding(ids: list[str], train_rows: list[dict[str, Any]], method: str, restarts: int, seed: int):
    np, minimize, _, _, _, _ = scientific_modules()
    index = {artifact_id: i for i, artifact_id in enumerate(ids)}
    constraints = indexed_constraints(train_rows, index)
    if not constraints:
        raise ValueError("no training constraints")
    array = np.asarray(constraints, dtype=int)
    n = len(ids)

    def objective(flat):
        coords = flat.reshape(n, 2)
        a, p, q = coords[array[:, 0]], coords[array[:, 1]], coords[array[:, 2]]
        dp = np.sum((a - p) ** 2, axis=1)
        dn = np.sum((a - q) ** 2, axis=1)
        grad = np.zeros_like(coords)
        if method == "tste":
            qp, qn = 1.0 / (1.0 + dp), 1.0 / (1.0 + dn)
            prob = np.clip(qp / (qp + qn), 1e-12, 1.0)
            loss = -np.log(prob).mean()
            ddp = (1.0 - prob) / (1.0 + dp) / len(array)
            ddn = -(1.0 - prob) / (1.0 + dn) / len(array)
        elif method == "soe":
            active = (1.0 + dp - dn) > 0
            loss = np.maximum(0.0, 1.0 + dp - dn).mean()
            ddp = active.astype(float) / len(array)
            ddn = -active.astype(float) / len(array)
        else:
            raise ValueError("method must be tste or soe")
        gp = 2 * ddp[:, None] * (a - p)
        gn = 2 * ddn[:, None] * (a - q)
        np.add.at(grad, array[:, 0], gp + gn)
        np.add.at(grad, array[:, 1], -gp)
        np.add.at(grad, array[:, 2], -gn)
        centered = coords - coords.mean(axis=0, keepdims=True)
        loss += 1e-6 * float((centered ** 2).mean())
        grad += 2e-6 * centered / n
        return float(loss), grad.ravel()

    best = None
    for restart in range(restarts):
        rng = np.random.default_rng(seed + restart)
        initial = rng.normal(0, 1e-2, size=(n, 2))
        result = minimize(objective, initial.ravel(), jac=True, method="L-BFGS-B", options={"maxiter": 1000})
        coords = result.x.reshape(n, 2)
        coords -= coords.mean(axis=0, keepdims=True)
        scale = float(np.sqrt((coords ** 2).sum() / max(1, n)))
        if scale:
            coords /= scale
        score = agreement(coords, constraints)
        candidate = (score, -float(result.fun), -restart, coords, result)
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    return best[3], {"train_agreement": best[0], "loss": -best[1], "restart": -best[2], "success": bool(best[4].success)}


def choose_basins(tste_coords, soe_coords, baseline_mask, requested: str, seed: int):
    np, _, _, KMeans, adjusted_rand_score, silhouette_score = scientific_modules()
    tste_base = tste_coords[baseline_mask]
    soe_base = soe_coords[baseline_mask]
    if len(tste_base) < 3:
        raise ValueError("at least three baseline artifacts are required")
    if requested != "auto":
        candidates = [int(requested)]
    else:
        upper = min(10, max(2, int(math.sqrt(len(tste_base))) + 1), len(tste_base) - 1)
        candidates = list(range(2, upper + 1))
    diagnostics = []
    for k in candidates:
        if not 1 < k < len(tste_base):
            raise ValueError("cluster count must be between 2 and baseline_count-1")
        tste_reference = KMeans(n_clusters=k, random_state=seed, n_init=30).fit_predict(tste_base)
        soe_reference = KMeans(n_clusters=k, random_state=seed, n_init=30).fit_predict(soe_base)
        silhouette_tste = float(silhouette_score(tste_base, tste_reference))
        silhouette_soe = float(silhouette_score(soe_base, soe_reference))
        stabilities = []
        for view_id, (base, reference) in enumerate(((tste_base, tste_reference), (soe_base, soe_reference))):
            agreements = []
            rng = np.random.default_rng(seed + 10_000 + k + view_id * 1_000)
            for repeat in range(20):
                sample = rng.integers(0, len(base), size=len(base))
                fitted = KMeans(n_clusters=k, random_state=seed + repeat + 1, n_init=10).fit(base[sample])
                labels = fitted.predict(base)
                agreements.append(float(adjusted_rand_score(reference, labels)))
            stabilities.append(float(np.mean(agreements)))
        cross_view_ari = float(adjusted_rand_score(tste_reference, soe_reference))
        min_cluster_size = min(int((tste_reference == label).sum()) for label in range(k))
        small_cluster_penalty = 0.25 if min_cluster_size < max(3, math.ceil(0.02 * len(tste_base))) else 0.0
        selection_score = (
            0.5 * (silhouette_tste + silhouette_soe)
            + 0.5 * cross_view_ari
            + 0.075 * sum(stabilities)
            - 0.01 * k
            - small_cluster_penalty
        )
        diagnostics.append(
            {
                "k": k,
                "silhouette_tste": silhouette_tste,
                "silhouette_soe": silhouette_soe,
                "stability_tste": stabilities[0],
                "stability_soe": stabilities[1],
                "cross_view_ari": cross_view_ari,
                "min_cluster_size": min_cluster_size,
                "selection_score": selection_score,
            }
        )
    selected = max(diagnostics, key=lambda row: (row["selection_score"], -row["k"]))
    tste_model = KMeans(n_clusters=selected["k"], random_state=seed, n_init=50).fit(tste_base)
    soe_model = KMeans(n_clusters=selected["k"], random_state=seed, n_init=50).fit(soe_base)
    labels = tste_model.predict(tste_coords)
    soe_labels = soe_model.predict(soe_coords)
    return labels, tste_model.cluster_centers_, soe_labels, selected, diagnostics


def align_to(reference, target):
    np, _, orthogonal_procrustes, _, _, _ = scientific_modules()
    ref = reference - reference.mean(axis=0)
    tar = target - target.mean(axis=0)
    rotation, scale = orthogonal_procrustes(tar, ref)
    aligned = tar @ rotation
    if scale:
        aligned *= np.linalg.norm(ref) / max(np.linalg.norm(aligned), 1e-12)
    return aligned


def eta_squared(scores, labels) -> float:
    np, *_ = scientific_modules()
    scores = np.asarray(scores, dtype=float)
    total = float(((scores - scores.mean()) ** 2).sum())
    if total == 0:
        return 0.0
    between = sum(float((scores[labels == label].mean() - scores.mean()) ** 2) * int((labels == label).sum()) for label in np.unique(labels))
    return between / total


def run_membership_agreement(rows, constraints, index) -> float:
    if not constraints:
        return float("nan")
    total = 0.0
    for row in constraints:
        anchor = rows[index[row["anchor"]]]["run_id"]
        positive = rows[index[row["positive"]]]["run_id"]
        negative = rows[index[row["negative"]]]["run_id"]
        same_positive, same_negative = anchor == positive, anchor == negative
        total += 1.0 if same_positive and not same_negative else 0.0 if same_negative and not same_positive else 0.5
    return total / len(constraints)


def map_metrics(rows, tste, soe, labels, soe_labels, centers, train, test, selected, diagnostics):
    np, _, _, _, adjusted_rand_score, _ = scientific_modules()
    ids = [row["artifact_id"] for row in rows]
    index = {value: i for i, value in enumerate(ids)}
    train_i, test_i = indexed_constraints(train, index), indexed_constraints(test, index)
    baseline_mask = np.asarray([bool(row["baseline"]) for row in rows])
    score_field = "analysis_score" if all(row.get("analysis_score") is not None for row in rows) else "score"
    scores = np.asarray([float(row[score_field]) for row in rows])
    run_to_indices: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        run_to_indices.setdefault(row["run_id"], []).append(i)
    distances, transitions = [], {}
    for run_id, members in run_to_indices.items():
        ordered = sorted(members, key=lambda i: int(rows[i]["sequence"]))
        for left, right in zip(ordered, ordered[1:]):
            distance = float(np.linalg.norm(tste[right] - tste[left]))
            if rows[left]["baseline"] and rows[right]["baseline"]:
                distances.append(distance)
            key = f"{labels[left]}->{labels[right]}"
            transitions[key] = transitions.get(key, 0) + 1
    median_step = float(np.median(distances)) if distances else None
    branch_jumps = []
    for child_index, row in enumerate(rows):
        parent_id = row.get("fork_point_id")
        if not parent_id or parent_id not in index:
            continue
        parent_index = index[parent_id]
        distance = float(np.linalg.norm(tste[child_index] - tste[parent_index]))
        branch_jumps.append(
            {
                "artifact_id": row["artifact_id"],
                "fork_point_id": parent_id,
                "selected": bool(row.get("selected", False)),
                "distance": distance,
                "normalized_distance": distance / median_step if median_step else None,
                "basin_changed": bool(labels[child_index] != labels[parent_index]),
            }
        )
    basin_summaries = []
    for basin in sorted(set(int(value) for value in labels)):
        mask = labels == basin
        basin_summaries.append({"basin": basin, "count": int(mask.sum()), "mean_score": float(scores[mask].mean()), "std_score": float(scores[mask].std())})
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_count": len(rows),
        "train_constraints": len(train),
        "heldout_constraints": len(test),
        "tste_train_agreement": agreement(tste, train_i),
        "tste_heldout_agreement": agreement(tste, test_i),
        "soe_train_agreement": agreement(soe, train_i),
        "soe_heldout_agreement": agreement(soe, test_i),
        "run_membership_heldout_agreement": run_membership_agreement(rows, test, index),
        "basin_count": int(selected["k"]),
        "basin_silhouette": selected["silhouette_tste"],
        "basin_silhouette_soe": selected["silhouette_soe"],
        "basin_stability": selected["stability_tste"],
        "basin_stability_soe": selected["stability_soe"],
        "tste_soe_basin_ari": float(adjusted_rand_score(labels[baseline_mask], soe_labels[baseline_mask])),
        "score_eta_squared": eta_squared(scores, labels),
        "score_field": score_field,
        "median_baseline_step": median_step,
        "branch_jumps": branch_jumps,
        "basins": basin_summaries,
        "transitions": transitions,
        "cluster_candidates": diagnostics,
    }


def write_coordinates(path: Path, rows, tste, soe, labels, soe_labels) -> None:
    fields = ["artifact_id", "run_id", "sequence", "score", "condition", "baseline", "basin", "soe_basin", "tste_x", "tste_y", "soe_x", "soe_y"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row, xy, uv, label, soe_label in zip(rows, tste, soe, labels, soe_labels):
            writer.writerow({
                "artifact_id": row["artifact_id"], "run_id": row["run_id"], "sequence": row["sequence"],
                "score": row["score"], "condition": row["condition"], "baseline": row["baseline"], "basin": int(label), "soe_basin": int(soe_label),
                "tste_x": float(xy[0]), "tste_y": float(xy[1]), "soe_x": float(uv[0]), "soe_y": float(uv[1]),
            })


def svg_map(rows, coords, labels, title: str) -> str:
    width, height, pad = 720, 480, 48
    xs, ys = [float(x[0]) for x in coords], [float(x[1]) for x in coords]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    sx = lambda x: pad + (x - xmin) / max(xmax - xmin, 1e-9) * (width - 2 * pad)
    sy = lambda y: height - pad - (y - ymin) / max(ymax - ymin, 1e-9) * (height - 2 * pad)
    palette = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#ca8a04", "#4f46e5", "#be123c", "#0f766e"]
    lines, points = [], []
    by_run: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        by_run.setdefault(row["run_id"], []).append(i)
    for members in by_run.values():
        ordered = sorted(members, key=lambda i: int(rows[i]["sequence"]))
        path = " ".join(("M" if j == 0 else "L") + f" {sx(xs[i]):.2f} {sy(ys[i]):.2f}" for j, i in enumerate(ordered))
        lines.append(f'<path d="{path}" fill="none" stroke="#94a3b8" stroke-width="1.2" opacity="0.65"/>')
    for i, row in enumerate(rows):
        color = palette[int(labels[i]) % len(palette)]
        radius = 5 if row["baseline"] else 7
        shape = f'<circle cx="{sx(xs[i]):.2f}" cy="{sy(ys[i]):.2f}" r="{radius}" fill="{color}" stroke="#fff" stroke-width="1.5">'
        tip = html.escape(f"{row['artifact_id']} | run={row['run_id']} | score={row['score']} | basin={labels[i]}")
        points.append(shape + f"<title>{tip}</title></circle>")
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}"><rect width="100%" height="100%" fill="#fff"/><text x="24" y="30" font-size="20" font-family="system-ui">{html.escape(title)}</text>{"".join(lines)}{"".join(points)}</svg>'


def write_dashboard(path: Path, rows, tste, soe, labels, soe_labels, metrics) -> None:
    score_label = "Within-campaign score" if metrics.get("score_field") == "analysis_score" else "Score"
    basin_rows = "".join(
        f"<tr><td>{row['basin']}</td><td>{row['count']}</td><td>{row['mean_score']:.6g}</td><td>{row['std_score']:.6g}</td></tr>"
        for row in metrics["basins"]
    )
    resolution_rows = "".join(
        f"<tr{' class=\"selected\"' if row['k'] == metrics['basin_count'] else ''}><td>{row['k']}</td>"
        f"<td>{row['silhouette_tste']:.3f}</td><td>{row['silhouette_soe']:.3f}</td>"
        f"<td>{row['stability_tste']:.3f}</td><td>{row['stability_soe']:.3f}</td>"
        f"<td>{row['cross_view_ari']:.3f}</td><td>{row['min_cluster_size']}</td><td>{row['selection_score']:.3f}</td></tr>"
        for row in metrics["cluster_candidates"]
    )
    path.write_text(f"""<!doctype html><meta charset="utf-8"><title>Idea-basin report</title>
<style>body{{font:15px system-ui;margin:32px;color:#172033;background:#f6f8fb}}main{{max-width:1500px;margin:auto}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.card,section{{background:white;border:1px solid #dbe2ea;border-radius:14px;padding:18px;margin:16px 0}}.value{{font-size:27px;font-weight:700}}.maps{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:8px;border-bottom:1px solid #e5e7eb}}tr.selected{{background:#dbeafe;font-weight:700}}@media(max-width:900px){{.maps,.cards{{grid-template-columns:1fr}}}}</style>
<main><h1>Validated idea-basin map</h1><p>Generated from blinded functional-similarity judgments. Clusters are fitted on baseline artifacts only.</p>
<div class="cards"><div class="card"><div>Artifacts</div><div class="value">{metrics['artifact_count']}</div></div><div class="card"><div>t-STE held-out</div><div class="value">{metrics['tste_heldout_agreement']:.3f}</div></div><div class="card"><div>SOE held-out</div><div class="value">{metrics['soe_heldout_agreement']:.3f}</div></div><div class="card"><div>Basins</div><div class="value">{metrics['basin_count']}</div></div></div>
<section class="maps"><div>{svg_map(rows,tste,labels,'t-STE trajectories')}</div><div>{svg_map(rows,soe,soe_labels,'SOE robustness map')}</div></section>
<section><h2>Validation</h2><table><tr><th>Measure</th><th>Value</th></tr><tr><td>Train constraints</td><td>{metrics['train_constraints']}</td></tr><tr><td>Held-out constraints</td><td>{metrics['heldout_constraints']}</td></tr><tr><td>Silhouette</td><td>{metrics['basin_silhouette']:.3f}</td></tr><tr><td>Clustering stability</td><td>{metrics['basin_stability']:.3f}</td></tr><tr><td>t-STE/SOE basin ARI</td><td>{metrics['tste_soe_basin_ari']:.3f}</td></tr><tr><td>Score variance explained by basin</td><td>{metrics['score_eta_squared']:.3f}</td></tr></table></section>
<section><h2>Resolution sweep</h2><p>The highlighted row is the selected resolution. Higher resolutions remain visible as possible fine-grained maps.</p><table><tr><th>k</th><th>t-STE silhouette</th><th>SOE silhouette</th><th>t-STE stability</th><th>SOE stability</th><th>Cross-view ARI</th><th>Smallest basin</th><th>Joint score</th></tr>{resolution_rows}</table></section>
<section><h2>Basin scores</h2><table><tr><th>Basin</th><th>Artifacts</th><th>Mean {score_label}</th><th>SD</th></tr>{basin_rows}</table></section></main>""", encoding="utf-8")


def fit_map(args: argparse.Namespace) -> dict[str, Any]:
    sampled = read_jsonl(args.artifacts)
    if args.constraints:
        constraints = read_jsonl(args.constraints)
        train = [row for row in constraints if row.get("split") == "train"]
        test = [row for row in constraints if row.get("split") == "heldout"]
        if len(train) + len(test) != len(constraints):
            raise ValueError("every direct constraint must have split=train or split=heldout")
    elif args.train_triplets_npy or args.test_triplets_npy:
        if not args.train_triplets_npy or not args.test_triplets_npy:
            raise ValueError("both train and test triplet arrays are required")
        np, *_ = scientific_modules()
        ids_for_index = [row["artifact_id"] for row in sampled]

        def from_array(path: Path) -> list[dict[str, Any]]:
            array = np.load(path)
            if array.ndim != 2 or array.shape[1] != 3:
                raise ValueError(f"{path}: expected an N x 3 triplet array")
            if len(array) and (array.min() < 0 or array.max() >= len(ids_for_index)):
                raise ValueError(f"{path}: triplet index outside artifact range")
            return [
                {"anchor": ids_for_index[int(a)], "positive": ids_for_index[int(p)], "negative": ids_for_index[int(n)]}
                for a, p, n in array
            ]

        train, test = from_array(args.train_triplets_npy), from_array(args.test_triplets_npy)
    else:
        if not args.queries or not args.judgments:
            raise ValueError("provide queries+judgments, direct constraints, or train+test triplet arrays")
        constraints = expand_judgments(read_jsonl(args.queries), read_jsonl(args.judgments))
        train, test = split_constraints(constraints, args.holdout)
    if not train or not test:
        raise ValueError("both training and held-out constraints must be nonempty")
    ids = [row["artifact_id"] for row in sampled]
    if len(ids) != len(set(ids)):
        raise ValueError("artifact IDs must be unique")
    observed = {x for row in train + test for x in (row["anchor"], row["positive"], row["negative"])}
    rows = [row for row in sampled if row["artifact_id"] in observed]
    ids = [row["artifact_id"] for row in rows]
    tste, tste_fit = fit_embedding(ids, train, "tste", args.restarts, args.seed)
    soe, soe_fit = fit_embedding(ids, train, "soe", args.restarts, args.seed + 1000)
    soe = align_to(tste, soe)
    np, *_ = scientific_modules()
    baseline_mask = np.asarray([bool(row["baseline"]) for row in rows])
    labels, centers, soe_labels, selected, diagnostics = choose_basins(tste, soe, baseline_mask, args.clusters, args.seed)
    metrics = map_metrics(rows, tste, soe, labels, soe_labels, centers, train, test, selected, diagnostics)
    metrics["tste_fit"] = tste_fit
    metrics["soe_fit"] = soe_fit
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    write_coordinates(output / "coordinates.csv", rows, tste, soe, labels, soe_labels)
    write_json(output / "metrics.json", metrics)
    with (output / "constraints.jsonl").open("w", encoding="utf-8") as handle:
        for split, values in (("train", train), ("heldout", test)):
            for row in values:
                handle.write(json.dumps({**row, "split": split}, ensure_ascii=False) + "\n")
    write_dashboard(output / "dashboard.html", rows, tste, soe, labels, soe_labels, metrics)
    return {"output": str(output), "metrics": metrics}


def status_run(run_dir: Path) -> dict[str, Any]:
    contract = read_json(run_dir / "contract.json")
    checkpoints = read_jsonl(run_dir / "checkpoints.jsonl")
    events = read_jsonl(run_dir / "events.jsonl")
    usage = max((float(row.get("cumulative_usage", 0)) for row in events + checkpoints), default=0.0)
    valid = [row for row in checkpoints if row.get("valid") is True]
    best = None
    if valid:
        best = select_checkpoint(valid, contract["direction"], budget=contract["budget"])["winner"]
    return {
        "task_id": contract["task_id"],
        "budget": contract["budget"],
        "budget_unit": contract["budget_unit"],
        "cumulative_usage": usage,
        "fraction_used": usage / contract["budget"],
        "checkpoint_count": len(checkpoints),
        "event_count": len(events),
        "best_in_budget": best,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="emit a Fork-and-Flush schedule")
    plan.add_argument("--budget", type=int, required=True)
    plan.add_argument("--unit", choices=("credits", "tokens", "cost-microusd"), default="credits")
    plan.add_argument("--branches", type=int, default=4)
    plan.add_argument("--branch-fraction", type=float, default=0.06)
    plan.add_argument("--triggers", type=float, nargs="+", default=[0.0, 0.33, 0.66])

    init = commands.add_parser("init-run", help="create a run contract and ledger")
    init.add_argument("--run-dir", type=Path, required=True)
    init.add_argument("--task-id", required=True)
    init.add_argument("--budget", type=int, required=True)
    init.add_argument("--unit", choices=("credits", "tokens", "cost-microusd"), default="credits")
    init.add_argument("--direction", choices=("max", "min"), default="max")
    init.add_argument("--evaluator", required=True)
    init.add_argument("--evaluator-version", default="unspecified")
    init.add_argument("--usage-source", default="coordinator-estimate")
    init.add_argument("--model", default="host-configured")
    init.add_argument("--reasoning-effort", default="host-configured")
    init.add_argument("--snapshot", default="working-tree")
    init.add_argument("--branches", type=int, default=4)
    init.add_argument("--branch-fraction", type=float, default=0.06)
    init.add_argument("--triggers", type=float, nargs="+", default=[0.0, 0.33, 0.66])

    record = commands.add_parser("record-checkpoint", help="validate and append a checkpoint JSON object")
    record.add_argument("--run-dir", type=Path, required=True)
    record.add_argument("--json", type=Path, required=True)
    record.add_argument("--require-files", action="store_true")

    select = commands.add_parser("select", help="select the best valid in-budget checkpoint")
    select.add_argument("records", type=Path)
    select.add_argument("--direction", choices=("max", "min"), default="max")
    select.add_argument("--incumbent")
    select.add_argument("--budget", type=float)
    select.add_argument("--require-files", action="store_true")

    status = commands.add_parser("status", help="summarize an initialized run")
    status.add_argument("--run-dir", type=Path, required=True)

    sample = commands.add_parser("sample-artifacts", help="deduplicate and evenly sample graded artifacts")
    sample.add_argument("artifacts", type=Path)
    sample.add_argument("--output", type=Path, required=True)
    sample.add_argument("--manifest", type=Path)
    sample.add_argument("--max-per-run", type=int, default=51)

    specs = commands.add_parser("make-spec-prompts", help="create canonical functional-specification prompts")
    specs.add_argument("sampled", type=Path)
    specs.add_argument("--task", required=True)
    specs.add_argument("--mechanisms", required=True)
    specs.add_argument("--output", type=Path, required=True)
    specs.add_argument("--max-bytes", type=int, default=200000)

    triplets = commands.add_parser("make-triplets", help="create blinded ordinal similarity prompts")
    triplets.add_argument("sampled", type=Path)
    triplets.add_argument("specifications", type=Path)
    triplets.add_argument("--task", required=True)
    triplets.add_argument("--mechanisms", required=True)
    triplets.add_argument("--count", type=int, default=6375)
    triplets.add_argument("--seed", type=int, default=0)
    triplets.add_argument("--output", type=Path, required=True)

    batch = commands.add_parser("run-prompt-batch", help="run prompts through an explicit stdin/stdout model wrapper")
    batch.add_argument("prompts", type=Path)
    batch.add_argument("--output", type=Path, required=True)
    batch.add_argument("--command", required=True, help="executable and arguments; prompt is sent on stdin")
    batch.add_argument("--mode", choices=("specification", "judgment"), required=True)
    batch.add_argument("--jobs", type=int, default=1)
    batch.add_argument("--timeout", type=float, default=300.0)

    embed = commands.add_parser("fit-map", help="fit t-STE/SOE, basins, metrics, and dashboard")
    embed.add_argument("--artifacts", type=Path, required=True)
    embed.add_argument("--queries", type=Path)
    embed.add_argument("--judgments", type=Path)
    embed.add_argument("--constraints", type=Path, help="pre-split JSONL anchor/positive/negative constraints")
    embed.add_argument("--train-triplets-npy", type=Path, help="paper-style N x 3 train index array")
    embed.add_argument("--test-triplets-npy", type=Path, help="paper-style N x 3 held-out index array")
    embed.add_argument("--output", type=Path, required=True)
    embed.add_argument("--clusters", default="auto", help="integer for paper reproduction or auto extension")
    embed.add_argument("--holdout", type=float, default=0.2)
    embed.add_argument("--restarts", type=int, default=8)
    embed.add_argument("--seed", type=int, default=0)

    commands.add_parser("doctor", help="check optional scientific dependencies")
    return parser


def emit_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan_schedule(args.budget, args.branches, args.branch_fraction, tuple(args.triggers), args.unit)
        elif args.command == "init-run":
            result = init_run(args)
        elif args.command == "record-checkpoint":
            record = validate_checkpoint(read_json(args.json), args.require_files)
            existing = read_jsonl(args.run_dir / "checkpoints.jsonl")
            if any(row.get("checkpoint") == record["checkpoint"] for row in existing):
                raise ValueError(f"duplicate checkpoint: {record['checkpoint']}")
            append_jsonl(args.run_dir / "checkpoints.jsonl", record)
            result = {"recorded": record["checkpoint"]}
        elif args.command == "select":
            result = select_checkpoint(read_jsonl(args.records), args.direction, args.incumbent, args.budget, args.require_files)
        elif args.command == "status":
            result = status_run(args.run_dir)
        elif args.command == "sample-artifacts":
            sampled, manifest = sample_artifacts(read_jsonl(args.artifacts), args.max_per_run)
            emit_jsonl(args.output, sampled)
            if args.manifest:
                write_json(args.manifest, manifest)
            result = manifest
        elif args.command == "make-spec-prompts":
            rows = make_spec_prompts(read_jsonl(args.sampled), args.task, args.mechanisms, args.max_bytes)
            emit_jsonl(args.output, rows)
            result = {"prompt_count": len(rows), "output": str(args.output)}
        elif args.command == "make-triplets":
            rows = make_triplet_queries(read_jsonl(args.sampled), read_jsonl(args.specifications), args.count, args.seed, args.task, args.mechanisms)
            emit_jsonl(args.output, rows)
            result = {"query_count": len(rows), "output": str(args.output)}
        elif args.command == "run-prompt-batch":
            result = run_prompt_batch(read_jsonl(args.prompts), args.output, args.command, args.mode, args.jobs, args.timeout)
        elif args.command == "fit-map":
            result = fit_map(args)
        elif args.command == "doctor":
            scientific_modules()
            result = {"ok": True, "required": ["numpy", "scipy", "scikit-learn"]}
        else:
            raise AssertionError(args.command)
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
