class CheckerError(Exception):
    """Base class for expected checker failures."""


class ConfigurationError(CheckerError):
    """The request or descriptor configuration is invalid."""


class MediaError(CheckerError):
    """Media could not be opened, decoded, or converted."""


class MetricError(CheckerError):
    """One requested metric could not be calculated."""
