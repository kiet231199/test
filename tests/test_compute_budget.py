import unittest
from types import SimpleNamespace

from media_checker.compute_budget import (
    DEFAULT_COMPUTE_BUDGET,
    EFFORT_LEVELS,
    compute_budget,
)
from media_checker.errors import ConfigurationError


class ComputeBudgetTests(unittest.TestCase):
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

    def test_unknown_effort_is_rejected(self):
        for effort in ("extreme", None):
            with self.subTest(effort = effort):
                with self.assertRaisesRegex(
                    ConfigurationError,
                    "Unsupported effort",
                ):
                    compute_budget(effort)
