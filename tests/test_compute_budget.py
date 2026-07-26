import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from media_checker.compute_budget import (
    DEFAULT_COMPUTE_BUDGET,
    EFFORT_LEVELS,
    compute_budget,
)
from media_checker.errors import ConfigurationError


class ComputeBudgetTests(unittest.TestCase):
    def test_cli_import_does_not_initialize_numpy(self):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(
            Path(__file__).resolve().parents[1] / "src"
        )
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import media_checker.cli; "
                    "raise SystemExit('numpy' in sys.modules)"
                ),
            ],
            env = environment,
            check = False,
        )

        self.assertEqual(process.returncode, 0)

    def test_effort_levels_configure_every_native_stage(self):
        cases = (
            ("light", 1),
            ("medium", 4),
            ("high", 0),
        )

        self.assertEqual(EFFORT_LEVELS, ("light", "medium", "high"))
        self.assertEqual(DEFAULT_COMPUTE_BUDGET, compute_budget("medium"))

        for effort, expected_threads in cases:
            with self.subTest(effort = effort):
                codec_context = SimpleNamespace(thread_count = None)
                graph = SimpleNamespace(threads = None)
                budget = compute_budget(effort)

                budget.configure_decoder(codec_context)
                budget.configure_filter_graph(graph)

                self.assertEqual(budget.name, effort)
                self.assertEqual(codec_context.thread_count, expected_threads)
                self.assertEqual(graph.threads, expected_threads)
    def test_numpy_is_loaded_with_one_copy_thread(self):
        with patch.dict("os.environ", {
            "OPENBLAS_NUM_THREADS" : "32",
            "OMP_NUM_THREADS"      : "32",
            "MKL_NUM_THREADS"      : "32",
            "NUMEXPR_NUM_THREADS"  : "32",
        }, clear = False), patch(
            "media_checker.compute_budget.importlib.import_module",
            return_value = object(),
        ) as import_module:
            compute_budget("high").load_numpy()

            import_module.assert_called_once_with("numpy")
            self.assertEqual(
                {
                    name : __import__("os").environ[name]
                    for name in (
                        "OPENBLAS_NUM_THREADS",
                        "OMP_NUM_THREADS",
                        "MKL_NUM_THREADS",
                        "NUMEXPR_NUM_THREADS",
                    )
                },
                {
                    "OPENBLAS_NUM_THREADS" : "1",
                    "OMP_NUM_THREADS"      : "1",
                    "MKL_NUM_THREADS"      : "1",
                    "NUMEXPR_NUM_THREADS"  : "1",
                },
            )

    def test_unknown_effort_is_rejected(self):
        for effort in ("extreme", None):
            with self.subTest(effort = effort):
                with self.assertRaisesRegex(
                    ConfigurationError,
                    "Unsupported effort",
                ):
                    compute_budget(effort)
