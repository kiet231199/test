import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from media_checker.native_runtime import (
    NATIVE_THREADS,
    configure_decoder,
    configure_filter_graph,
    load_numpy,
)


class NativeRuntimeTests(unittest.TestCase):
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

    def test_native_stages_use_one_thread(self):
        codec_context = SimpleNamespace(thread_count = None)
        graph = SimpleNamespace(threads = None)

        configure_decoder(codec_context)
        configure_filter_graph(graph)

        self.assertEqual(NATIVE_THREADS, 1)
        self.assertEqual(codec_context.thread_count, 1)
        self.assertEqual(graph.threads, 1)

    def test_numpy_is_loaded_with_one_copy_thread(self):
        with patch.dict("os.environ", {
            "OPENBLAS_NUM_THREADS" : "32",
            "OMP_NUM_THREADS"      : "32",
            "MKL_NUM_THREADS"      : "32",
            "NUMEXPR_NUM_THREADS"  : "32",
        }, clear = False), patch(
            "media_checker.native_runtime.importlib.import_module",
            return_value = object(),
        ) as import_module:
            load_numpy()

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
