from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fnf_cross_task", ROOT / "scripts" / "fnf.py")
fnf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fnf)


class CrossTaskIntegrationTests(unittest.TestCase):
    def test_controller_is_task_and_metric_direction_agnostic(self):
        cases = [
            {
                "task_id": "circle-packing-9",
                "unit": "tokens",
                "direction": "max",
                "scores": [0.710, 0.734, 0.721],
                "winner": "candidate-1",
            },
            {
                "task_id": "route-length",
                "unit": "cost-microusd",
                "direction": "min",
                "scores": [120.0, 98.0, 105.0],
                "winner": "candidate-1",
            },
            {
                "task_id": "dclm-validation",
                "unit": "credits",
                "direction": "max",
                "scores": [0.350, 0.391, 0.382],
                "winner": "candidate-1",
            },
        ]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for case in cases:
                with self.subTest(task_id=case["task_id"]):
                    run_dir = root / case["task_id"]
                    args = argparse.Namespace(
                        run_dir=run_dir,
                        task_id=case["task_id"],
                        budget=10_000,
                        unit=case["unit"],
                        direction=case["direction"],
                        evaluator="python evaluate.py",
                        evaluator_version="fixture-v1",
                        usage_source="measured",
                        model="runtime-adapter",
                        reasoning_effort="high",
                        snapshot="fixture-snapshot",
                        branches=4,
                        branch_fraction=0.06,
                        triggers=[0.0, 0.33, 0.66],
                    )
                    initialized = fnf.init_run(args)
                    status = fnf.status_run(run_dir)

                    self.assertEqual(status["task_id"], case["task_id"])
                    self.assertEqual(status["fraction_used"], 0)
                    self.assertEqual(
                        sum(phase["allocation"] for phase in initialized["schedule"]["phases"]),
                        10_000,
                    )

                    records = [
                        {
                            "checkpoint": f"candidate-{index}",
                            "lineage": f"lineage-{index}",
                            "score": score,
                            "valid": True,
                            "artifact": f"artifacts/candidate-{index}",
                            "evidence": f"evidence/candidate-{index}.json",
                            "cumulative_usage": (index + 1) * 100,
                        }
                        for index, score in enumerate(case["scores"])
                    ]
                    records.extend(
                        [
                            {
                                "checkpoint": "invalid",
                                "lineage": "invalid-lineage",
                                "score": 999.0,
                                "valid": False,
                                "artifact": "artifacts/invalid",
                                "evidence": "evidence/invalid.json",
                                "cumulative_usage": 10,
                            },
                            {
                                "checkpoint": "after-budget",
                                "lineage": "late-lineage",
                                "score": 999.0,
                                "valid": True,
                                "artifact": "artifacts/after-budget",
                                "evidence": "evidence/after-budget.json",
                                "cumulative_usage": 10_001,
                            },
                        ]
                    )

                    selected = fnf.select_checkpoint(
                        records,
                        case["direction"],
                        budget=10_000,
                    )
                    self.assertEqual(selected["winner"]["checkpoint"], case["winner"])
                    self.assertEqual(
                        {row["reason"] for row in selected["rejected"]},
                        {"invalid", "after_budget"},
                    )


if __name__ == "__main__":
    unittest.main()
