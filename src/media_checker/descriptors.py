import os
import re
from dataclasses import dataclass
from fractions import Fraction
from math import gcd
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

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
    DescriptorSet,
    MediaDescriptor,
)


ENV_FIELD       = "env"
INPUT_FIELD     = "input"
REFERENCE_FIELD = "reference"
PATH_FIELD      = "path"

RAW_FIELDS = (
    "width",
    "height",
    "framerate",
    "format",
    "frame_count",
    "stride",
    "sliceheight",
)

INTEGER_FIELDS = frozenset((
    "width",
    "height",
    "frame_count",
    "stride",
    "sliceheight",
))

ENV_NAME_PATTERN  = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_TOKEN_PATTERN = re.compile(r"\$\{([^{}]*)\}")


@dataclass(frozen = True)
class _EnvironmentValue:
    value          : Any
    system_derived : bool


def load_descriptor(descriptor_path: Path) -> DescriptorSet:
    """Load one document containing input and optional reference media."""

    descriptor_path = descriptor_path.resolve()
    values          = _load_yaml(descriptor_path)
    environment     = _descriptor_environment(values.get(ENV_FIELD, {}))

    input_values = values.get(INPUT_FIELD)

    if not isinstance(input_values, dict):
        raise ConfigurationError(
            "Descriptor field 'input' must contain a YAML mapping"
        )

    input_descriptor = _media_descriptor(
        input_values,
        descriptor_path,
        environment,
        INPUT_FIELD,
    )

    reference_values = values.get(REFERENCE_FIELD)
    reference_descriptor = None  # type: Optional[MediaDescriptor]

    if reference_values is not None:
        if not isinstance(reference_values, dict):
            raise ConfigurationError(
                "Descriptor field 'reference' must contain a YAML mapping or null"
            )

        reference_descriptor = _media_descriptor(
            reference_values,
            descriptor_path,
            environment,
            REFERENCE_FIELD,
        )

    return DescriptorSet(
        input     = input_descriptor,
        reference = reference_descriptor,
    )


def _load_yaml(descriptor_path: Path) -> Dict[str, Any]:
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

    return values


def _descriptor_environment(value: Any) -> Dict[str, _EnvironmentValue]:
    if not isinstance(value, dict):
        raise ConfigurationError("Descriptor field 'env' must contain a YAML mapping")

    system_environment = {
        name : _EnvironmentValue(raw_value, True)
        for name, raw_value in os.environ.items()
    }
    descriptor_values = {}  # type: Dict[str, _EnvironmentValue]

    for name, raw_value in value.items():
        if not isinstance(name, str) or not ENV_NAME_PATTERN.fullmatch(name):
            raise ConfigurationError(
                "Descriptor environment variable name '{}' is invalid".format(name)
            )

        if not _is_scalar(raw_value):
            raise ConfigurationError(
                "Descriptor environment variable '{}' must be a scalar value".format(
                    name
                )
            )

        expanded_value = raw_value
        system_derived = False

        if isinstance(raw_value, str):
            expanded_value, _, system_derived = _expand_string(
                raw_value,
                system_environment,
                "environment variable '{}'".format(name),
            )

        descriptor_values[name] = _EnvironmentValue(
            expanded_value,
            system_derived,
        )

    environment = dict(system_environment)

    for name, expanded_value in descriptor_values.items():
        if expanded_value.value is None:
            environment.pop(name, None)
        else:
            environment[name] = expanded_value

    return environment


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _media_descriptor(
    values: Mapping[str, Any],
    descriptor_path: Path,
    environment: Mapping[str, _EnvironmentValue],
    section_name: str,
) -> MediaDescriptor:
    path_value = _field_value(
        values.get(PATH_FIELD),
        PATH_FIELD,
        environment,
        section_name,
    )
    path      = _media_path(path_value, descriptor_path, section_name)
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

    descriptor = _raw_descriptor(
        path,
        extension,
        values,
        environment,
        section_name,
    )
    validate_raw_file(descriptor)
    return descriptor


def _field_value(
    value: Any,
    field_name: str,
    environment: Mapping[str, _EnvironmentValue],
    section_name: str,
) -> Any:
    if not isinstance(value, str):
        return value

    expanded, full_replacement, system_derived = _expand_string(
        value,
        environment,
        "field '{}.{}'".format(section_name, field_name),
    )

    if not full_replacement:
        return expanded

    if (
        full_replacement
        and system_derived
        and field_name in INTEGER_FIELDS
        and isinstance(expanded, str)
    ):
        if re.fullmatch(r"[+-]?[0-9]+", expanded):
            return int(expanded, 10)

    return expanded


def _expand_string(
    value: str,
    environment: Mapping[str, _EnvironmentValue],
    location: str,
) -> Tuple[Any, bool, bool]:
    matches = list(ENV_TOKEN_PATTERN.finditer(value))
    unmatched_text = ENV_TOKEN_PATTERN.sub("", value)

    if "${" in unmatched_text:
        raise ConfigurationError(
            "Descriptor {} contains a malformed environment variable".format(location)
        )

    for match in matches:
        name = match.group(1)

        if not ENV_NAME_PATTERN.fullmatch(name):
            raise ConfigurationError(
                "Descriptor {} contains invalid environment variable '${{{}}}'".format(
                    location,
                    name,
                )
            )

        if name not in environment:
            raise ConfigurationError(
                "Descriptor {} references missing environment variable '{}'".format(
                    location,
                    name,
                )
            )

    full_match = ENV_TOKEN_PATTERN.fullmatch(value)

    if full_match is not None:
        environment_value = environment[full_match.group(1)]
        return (
            environment_value.value,
            True,
            environment_value.system_derived,
        )

    if not matches:
        return value, False, False

    fields = []
    position = 0

    for match in matches:
        fields.append(value[position:match.start()])
        fields.append(str(environment[match.group(1)].value))
        position = match.end()

    fields.append(value[position:])
    return "".join(fields), False, False


def _media_path(value: Any, descriptor_path: Path, section_name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(
            "Descriptor field '{}.path' must be a non-empty string".format(
                section_name
            )
        )

    path = Path(value)

    if not path.is_absolute():
        path = descriptor_path.parent / path

    return path.resolve()


def _raw_descriptor(
    path: Path,
    extension: str,
    values: Mapping[str, Any],
    environment: Mapping[str, _EnvironmentValue],
    section_name: str,
) -> MediaDescriptor:
    missing_fields = [field for field in RAW_FIELDS if field not in values]

    if missing_fields:
        raise ConfigurationError(
            "Raw descriptor is missing field(s): {}".format(
                ", ".join(missing_fields)
            )
        )

    expanded_values = {
        field : _field_value(values[field], field, environment, section_name)
        for field in RAW_FIELDS
    }
    raw_format = expanded_values["format"]

    if not isinstance(raw_format, str) or not raw_format.strip():
        raise ConfigurationError("Raw descriptor field 'format' must be a string")

    raw_format = raw_format.upper()

    if raw_format not in RAW_FORMATS:
        raise ConfigurationError("Unsupported raw format '{}'".format(raw_format))

    return MediaDescriptor(
        path        = path,
        media_type  = RAW_MEDIA_TYPE,
        extension   = extension,
        width       = _positive_integer(expanded_values["width"], "width"),
        height      = _positive_integer(expanded_values["height"], "height"),
        framerate   = _framerate(expanded_values["framerate"]),
        format      = raw_format,
        frame_count = _positive_integer(
            expanded_values["frame_count"],
            "frame_count",
        ),
        stride      = _positive_integer(expanded_values["stride"], "stride"),
        sliceheight = _positive_integer(
            expanded_values["sliceheight"],
            "sliceheight",
        ),
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
