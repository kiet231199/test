from dataclasses import dataclass
from typing import Any, Dict, Tuple

from media_checker.errors import ConfigurationError


DEFAULT_EFFORT = "medium"


@dataclass(frozen = True)
class ComputeBudget:
    """Configure native compute stages for one public effort level."""

    name            : str
    decoder_threads : int
    filter_threads  : int

    def configure_decoder(self, codec_context: Any) -> None:
        codec_context.thread_count = self.decoder_threads

    def configure_filter_graph(self, graph: Any) -> None:
        graph.threads = self.filter_threads


_COMPUTE_BUDGETS = {
    "light"  : ComputeBudget("light", 1, 1),
    "medium" : ComputeBudget("medium", 4, 4),
    "high"   : ComputeBudget("high", 0, 0),
}  # type: Dict[str, ComputeBudget]

EFFORT_LEVELS = tuple(_COMPUTE_BUDGETS)  # type: Tuple[str, ...]
DEFAULT_COMPUTE_BUDGET = _COMPUTE_BUDGETS[DEFAULT_EFFORT]


def compute_budget(effort: str) -> ComputeBudget:
    """Resolve one validated public effort level."""

    if not isinstance(effort, str) or effort not in _COMPUTE_BUDGETS:
        raise ConfigurationError(
            "Unsupported effort '{}'".format(effort)
        )

    return _COMPUTE_BUDGETS[effort]
