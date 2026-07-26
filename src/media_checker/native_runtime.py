import importlib
import os


NATIVE_THREADS = 1


def configure_decoder(codec_context) -> None:
    codec_context.thread_count = NATIVE_THREADS


def configure_filter_graph(graph) -> None:
    graph.threads = NATIVE_THREADS


def load_numpy():
    """Load NumPy after constraining copy-only native worker pools."""

    thread_count = str(NATIVE_THREADS)

    for variable in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[variable] = thread_count

    return importlib.import_module("numpy")
