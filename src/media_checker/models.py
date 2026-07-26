from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

RAW_MEDIA_TYPE     = "raw"
ENCODED_MEDIA_TYPE = "encoded"

STATUS_SUCCESS     = "success"
STATUS_PARTIAL     = "partial"
STATUS_FAILED      = "failed"
STATUS_ERROR       = "error"
STATUS_NOT_CHECKED = "not checked"

@dataclass(frozen = True)
class MediaDescriptor:
    """Validated media path and optional raw-video layout."""

    path        : Path
    media_type  : str
    extension   : str
    width       : Optional[int] = None
    height      : Optional[int] = None
    framerate   : Optional[Fraction] = None
    format      : Optional[str] = None
    frame_count : Optional[int] = None
    stride      : Optional[int] = None
    sliceheight : Optional[int] = None

    @property
    def is_raw(self) -> bool:
        return self.media_type == RAW_MEDIA_TYPE


@dataclass(frozen = True)
class DescriptorSet:
    """Validated input media and optional comparison reference."""

    input     : MediaDescriptor
    reference : Optional[MediaDescriptor]


@dataclass(frozen = True)
class CheckRequest:
    """One subject, optional reference, and ordered metrics to calculate."""

    input     : MediaDescriptor
    reference : Optional[MediaDescriptor]
    metrics   : Tuple[str, ...]


@dataclass(frozen = True)
class VideoMetadata:
    width        : int
    height       : int
    framerate    : Optional[Fraction]
    format       : Optional[str]
    profile      : Optional[str] = None
    level        : Optional[int] = None
    codec_name   : Optional[str] = None


@dataclass(frozen = True)
class MetricResult:
    status : str
    value  : Optional[Any] = None

    @classmethod
    def success(cls, value: Any) -> "MetricResult":
        return cls(status = STATUS_SUCCESS, value = value)

    @classmethod
    def failure(cls, message: str) -> "MetricResult":
        return cls(status = STATUS_ERROR, value = message)

    @classmethod
    def not_checked(cls) -> "MetricResult":
        return cls(status = STATUS_NOT_CHECKED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status" : self.status,
            "value"  : self.value,
        }


@dataclass(frozen = True)
class CheckResult:
    """Overall status and independent result for each requested metric."""

    status  : str
    metrics : Dict[str, MetricResult] = field(default_factory = dict)
    error   : Optional[str] = None

    @classmethod
    def configuration_failure(cls, message: str) -> "CheckResult":
        return cls(status = STATUS_FAILED, error = message)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "status" : self.status,
        }  # type: Dict[str, Any]

        if self.error is not None:
            result["error"] = self.error

        result["metrics"] = {
            name : metric.to_dict()
            for name, metric in self.metrics.items()
        }

        return result
