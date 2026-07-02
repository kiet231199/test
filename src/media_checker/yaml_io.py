import os
import tempfile
from pathlib import Path

import yaml

from media_checker.models import CheckResult


def write_result(result: CheckResult, output_path: Path) -> None:
    """Atomically serialize a check result as ordered YAML."""

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents = True, exist_ok = True)

    descriptor, temporary_name = tempfile.mkstemp(
        dir    = str(output_path.parent),
        prefix = ".{}-".format(output_path.name),
        suffix = ".tmp",
    )

    try:
        with os.fdopen(descriptor, "w", encoding = "utf-8") as output_file:
            yaml.safe_dump(
                result.to_dict(),
                output_file,
                sort_keys          = False,
                allow_unicode      = True,
                default_flow_style = False,
            )

        os.replace(temporary_name, output_path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
