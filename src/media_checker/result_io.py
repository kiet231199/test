import json
import os
import tempfile
from pathlib import Path

import yaml

from media_checker.errors import ConfigurationError
from media_checker.models import CheckResult


YAML_OUTPUT_EXTENSIONS = frozenset((".txt", ".yaml"))
JSON_OUTPUT_EXTENSION  = ".json"


def output_format(output_path: Path) -> str:
    """Validate an output suffix and return its serializer name."""

    extension = output_path.suffix.lower()

    if extension in YAML_OUTPUT_EXTENSIONS:
        return "yaml"

    if extension == JSON_OUTPUT_EXTENSION:
        return "json"

    raise ConfigurationError(
        "Unsupported output extension '{}'; expected .txt, .yaml, or .json".format(
            extension or "<none>"
        )
    )


def write_result(result: CheckResult, output_path: Path) -> None:
    """Atomically serialize a check result according to its output suffix."""

    serializer = output_format(output_path)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents = True, exist_ok = True)

    descriptor, temporary_name = tempfile.mkstemp(
        dir    = str(output_path.parent),
        prefix = ".{}-".format(output_path.name),
        suffix = ".tmp",
    )

    try:
        with os.fdopen(descriptor, "w", encoding = "utf-8") as output_file:
            if serializer == "yaml":
                yaml.safe_dump(
                    result.to_dict(),
                    output_file,
                    sort_keys          = False,
                    allow_unicode      = True,
                    default_flow_style = False,
                )
            else:
                json.dump(
                    result.to_dict(),
                    output_file,
                    ensure_ascii = False,
                    allow_nan    = False,
                    indent       = 2,
                )
                output_file.write("\n")

        os.replace(temporary_name, output_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
