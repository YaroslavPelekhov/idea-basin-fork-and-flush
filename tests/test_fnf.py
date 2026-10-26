from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fnf", ROOT / "scripts" / "fnf.py")
fnf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fnf)


def checkpoint(name, score, usage=10, valid=True, lineage="worker"):
    return {
        "checkpoint": name,
        "lineage": lineage,
        "score": score,
        "valid": valid,
        "artifact": f"artifacts/{name}",
        "evidence": f"evidence/{name}.json",
        "cumulative_usage": usage,
    }


class ScheduleTests(unittest.TestCase):
    def test_paper_schedule_and_conservation(self):
        schedule = fnf.plan_schedule(10_000)
        self.assertEqual([phase["allocation"] for phase in schedule["phases"]], [2400, 900, 2400, 900, 2400, 1000])
        self.assertEqual(sum(phase["allocation"] for phase in schedule["phases"]), 10_000)
        self.assertEqual([phase["start"] for phase in schedule["phases"][::2]], [0, 3300, 6600])

    def test_rounding_conserves_budget(self):
        for budget in range(100, 500):
            schedule = fnf.plan_schedule(budget)
            self.assertEqual(sum(phase["allocation"] for phase in schedule["phases"]), budget)

    def test_invalid_schedule_rejected(self):
        for kwargs in ({"budget": 0}, {"budget": 100, "branches": 100}, {"budget": 1000, "triggers": (0.1, 0.5)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                fnf.plan_schedule(**kwargs)


class SelectionTests(unittest.TestCase):
    def test_best_ever_and_invalid(self):
        rows = [checkpoint("best", 14), checkpoint("last", 11), checkpoint("invalid", 99, valid=False)]
        for permutation in itertools.permutations(rows):
            winner = fnf.select_checkpoint(permutation, "max")["winner"]
            self.assertEqual(winner["checkpoint"], "best")

    def test_budget_filter_and_incumbent_tie(self):
        rows = [checkpoint("old", 5, 10), checkpoint("late", 100, 101), checkpoint("peer", 5, 20)]
        result = fnf.select_checkpoint(rows, "max", incumbent="old", budget=100)
        self.assertEqual(result["winner"]["checkpoint"], "old")
        self.assertEqual(result["rejected"], [{"checkpoint": "late", "reason": "after_budget"}])

    def test_minimize(self):
        winner = fnf.select_checkpoint([checkpoint("a", -2), checkpoint("b", -3)], "min")["winner"]
        self.assertEqual(winner["checkpoint"], "b")


class ArtifactTests(unittest.TestCase):
    def test_hash_dedupe_and_even_sampling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            for index in range(7):
                path = root / f"a{index}.txt"
                path.write_text("0" if index == 6 else str(index))
                rows.append({"artifact_id": f"a{index}", "run_id": "r", "sequence": index, "path": str(path), "score": index, "condition": "single-run", "baseline": True})
            sampled, manifest = fnf.sample_artifacts(rows, 3)
            self.assertEqual(manifest["exact_duplicates_removed"], 1)
            self.assertEqual([row["sequence"] for row in sampled], [0, 2, 5])

    def test_spec_prompt_excludes_score_and_contains_schema(self):
        prompt = fnf.specification_prompt("pack", "search", "source")
        self.assertIn("## 11. NOT IMPLEMENTED", prompt)
        self.assertIn("Exclude identifiers", prompt)


class JudgmentTests(unittest.TestCase):
    def setUp(self):
        self.query = {"query_id": "q", "a": "a", "b": "b", "c": "c"}

    def test_ranking_expands_to_three_constraints(self):
        constraints = fnf.expand_judgments([self.query], [{"query_id": "q", "order": ["AB", "AC", "BC"]}])
        self.assertEqual(len(constraints), 3)
        self.assertIn({"query_id": "q", "anchor": "a", "positive": "b", "negative": "c"}, constraints)

    def test_bad_order_rejected(self):
        with self.assertRaises(ValueError):
            fnf.expand_judgments([self.query], [{"query_id": "q", "order": ["AB", "AB", "BC"]}])

    def test_anchor_stratified_split(self):
        rows = []
        for anchor in "abc":
            for index in range(10):
                rows.append({"query_id": f"{anchor}{index}", "anchor": anchor, "positive": "p", "negative": "n"})
        train, test = fnf.split_constraints(rows, 0.2)
        self.assertEqual((len(train), len(test)), (24, 6))
        self.assertEqual({row["anchor"] for row in test}, {"a", "b", "c"})

    def test_prompt_batch_is_resumable(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "specs.jsonl"
            prompts = [{"artifact_id": "a", "prompt": "hello"}, {"artifact_id": "b", "prompt": "world"}]
            command = "python3 -c \"import sys; print(sys.stdin.read().upper())\""
            first = fnf.run_prompt_batch(prompts, output, command, "specification", 2, 10)
            second = fnf.run_prompt_batch(prompts, output, command, "specification", 2, 10)
            self.assertEqual((first["completed"], second["completed"], second["already_completed"]), (2, 0, 2))
            self.assertEqual({row["specification"] for row in fnf.read_jsonl(output)}, {"HELLO", "WORLD"})


class EndToEndMapTests(unittest.TestCase):
    def test_synthetic_two_basin_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            latent = {f"a{i}": (-3 + i * 0.1 if i < 5 else 3 + (i - 5) * 0.1) for i in range(10)}
            artifacts = []
            for index, artifact_id in enumerate(latent):
                artifacts.append({"artifact_id": artifact_id, "run_id": f"r{index % 2}", "sequence": index, "path": str(root), "score": latent[artifact_id], "condition": "single-run", "baseline": True})
            queries, judgments = [], []
            for number, triple in enumerate(itertools.combinations(latent, 3)):
                a, b, c = triple
                pairs = {"AB": abs(latent[a] - latent[b]), "AC": abs(latent[a] - latent[c]), "BC": abs(latent[b] - latent[c])}
                order = sorted(pairs, key=lambda pair: (pairs[pair], pair))
                query_id = f"q{number}"
                queries.append({"query_id": query_id, "a": a, "b": b, "c": c})
                judgments.append({"query_id": query_id, "order": order})
            fnf.emit_jsonl(root / "artifacts.jsonl", artifacts)
            fnf.emit_jsonl(root / "queries.jsonl", queries)
            fnf.emit_jsonl(root / "judgments.jsonl", judgments)
            args = argparse.Namespace(artifacts=root / "artifacts.jsonl", queries=root / "queries.jsonl", judgments=root / "judgments.jsonl", constraints=None, train_triplets_npy=None, test_triplets_npy=None, output=root / "map", clusters="2", holdout=0.2, restarts=3, seed=4)
            result = fnf.fit_map(args)
            self.assertGreater(result["metrics"]["tste_heldout_agreement"], 0.8)
            self.assertEqual(result["metrics"]["basin_count"], 2)
            self.assertTrue((root / "map" / "dashboard.html").is_file())

    def test_pre_split_numpy_triplets(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            latent = [-3.2, -3.0, -2.8, 2.8, 3.0, 3.2]
            artifacts = [
                {"artifact_id": f"a{i}", "run_id": f"r{i % 2}", "sequence": i, "path": str(root), "score": value, "condition": "single-run", "baseline": True}
                for i, value in enumerate(latent)
            ]
            triplets = []
            for anchor in range(len(latent)):
                for left in range(len(latent)):
                    for right in range(left + 1, len(latent)):
                        if anchor in {left, right}:
                            continue
                        positive, negative = (left, right) if abs(latent[anchor] - latent[left]) < abs(latent[anchor] - latent[right]) else (right, left)
                        triplets.append((anchor, positive, negative))
            fnf.emit_jsonl(root / "artifacts.jsonl", artifacts)
            np.save(root / "train.npy", np.asarray(triplets[:45], dtype=int))
            np.save(root / "test.npy", np.asarray(triplets[45:], dtype=int))
            args = argparse.Namespace(artifacts=root / "artifacts.jsonl", queries=None, judgments=None, constraints=None, train_triplets_npy=root / "train.npy", test_triplets_npy=root / "test.npy", output=root / "map", clusters="2", holdout=0.2, restarts=2, seed=5)
            result = fnf.fit_map(args)
            self.assertGreaterEqual(result["metrics"]["tste_heldout_agreement"], 0.75)


class RunTests(unittest.TestCase):
    def test_init_and_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = argparse.Namespace(run_dir=Path(temporary) / "run", task_id="demo", budget=1000, unit="tokens", direction="max", evaluator="python grade.py", evaluator_version="1", usage_source="measured", model="m", reasoning_effort="high", snapshot="abc", branches=4, branch_fraction=0.06, triggers=[0.0, 0.33, 0.66])
            fnf.init_run(args)
            status = fnf.status_run(args.run_dir)
            self.assertEqual(status["task_id"], "demo")
            self.assertEqual(status["fraction_used"], 0)


if __name__ == "__main__":
    unittest.main()
