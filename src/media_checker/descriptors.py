from fractions import Fraction
from math import gcd
from pathlib import Path
from typing import Any, Dict

import yaml

from media_checker.errors import ConfigurationError
from media_checker.media import (
    ENCODED_VIDEO_EXTENSIONS,
    RAW_FORMATS,
    SUPPORTED_EXTENSIONS,
    media_extension,
    validate_raw_file,
)
from media_checker.models import (
    ENCODED_MEDIA_TYPE,
    RAW_MEDIA_TYPE,
    MediaDescriptor,
)


PATH_FIELD = "path"

RAW_FIELDS = (
    "width",
    "height",
    "framerate",
    "format",
    "frame_count",
    "stride",
    "sliceheight",
)

ALLOWED_FIELDS = frozenset((PATH_FIELD,) + RAW_FIELDS)


def load_descriptor(descriptor_path: Path) -> MediaDescriptor:
    """Load and validate one encoded or raw YAML media descriptor."""

    descriptor_path = descriptor_path.resolve()

    try:
        content = descriptor_path.read_text(encoding = "utf-8")
    except OSError as error:
        raise ConfigurationError(
            "Cannot read descriptor '{}': {}".format(descriptor_path, error)
        ) from error

    try:
        values = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ConfigurationError(
            "Descriptor '{}' contains invalid YAML: {}".format(
                descriptor_path,
                error,
            )
        ) from error

    if not isinstance(values, dict):
        raise ConfigurationError(
            "Descriptor '{}' must contain a YAML mapping".format(descriptor_path)
        )

    _validate_fields(values)

    path = _media_path(values.get(PATH_FIELD), descriptor_path)
    extension = media_extension(path)

    if extension not in SUPPORTED_EXTENSIONS:
        raise ConfigurationError(
            "Unsupported media extension '{}'".format(extension or "<none>")
        )

    if not path.is_file():
        raise ConfigurationError("Media file does not exist: '{}'".format(path))

    if extension in ENCODED_VIDEO_EXTENSIONS:
        return MediaDescriptor(
            path       = path,
            media_type = ENCODED_MEDIA_TYPE,
            extension  = extension,
        )

    descriptor = _raw_descriptor(path, extension, values)
    validate_raw_file(descriptor)
    return descriptor


def _validate_fields(values: Dict[str, Any]) -> None:
    unknown_fields = sorted(set(values) - ALLOWED_FIELDS)

    if unknown_fields:
        raise ConfigurationError(
            "Descriptor contains unknown field(s): {}".format(
                ", ".join(unknown_fields)
            )
        )


def _media_path(value: Any, descriptor_path: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("Descriptor field 'path' must be a non-empty string")

    path = Path(value)

    if not path.is_absolute():
        path = descriptor_path.parent / path

    return path.resolve()


def _raw_descriptor(
    path: Path,
    extension: str,
    values: Dict[str, Any],
) -> MediaDescriptor:
    missing_fields = [field for field in RAW_FIELDS if field not in values]

    if missing_fields:
        raise ConfigurationError(
            "Raw descriptor is missing field(s): {}".format(
                ", ".join(missing_fields)
            )
        )

    raw_format = values["format"]

    if not isinstance(raw_format, str) or not raw_format.strip():
        raise ConfigurationError("Raw descriptor field 'format' must be a string")

    raw_format = raw_format.upper()

    if raw_format not in RAW_FORMATS:
        raise ConfigurationError("Unsupported raw format '{}'".format(raw_format))

    return MediaDescriptor(
        path        = path,
        media_type  = RAW_MEDIA_TYPE,
        extension   = extension,
        width       = _positive_integer(values["width"], "width"),
        height      = _positive_integer(values["height"], "height"),
        framerate   = _framerate(values["framerate"]),
        format      = raw_format,
        frame_count = _positive_integer(values["frame_count"], "frame_count"),
        stride      = _positive_integer(values["stride"], "stride"),
        sliceheight = _positive_integer(values["sliceheight"], "sliceheight"),
    )


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(
            "Raw descriptor field '{}' must be a positive integer".format(field_name)
        )

    return value


def _framerate(value: Any) -> Fraction:
    if not isinstance(value, str):
        raise ConfigurationError(
            "Raw descriptor field 'framerate' must use '<numerator>/<denominator>'"
        )

    fields = value.split("/")

    if len(fields) != 2 or not all(field.isdigit() for field in fields):
        raise ConfigurationError(
            "Raw descriptor field 'framerate' must use '<numerator>/<denominator>'"
        )

    numerator   = int(fields[0])
    denominator = int(fields[1])

    if numerator <= 0 or denominator <= 0:
        raise ConfigurationError("Raw descriptor framerate must be positive")

    if gcd(numerator, denominator) != 1:
        raise ConfigurationError("Raw descriptor framerate must be canonical")

    return Fraction(numerator, denominator)
