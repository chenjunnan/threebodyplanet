class ThreeBodyError(Exception):
    """Base class for user-facing errors."""


class ConfigError(ThreeBodyError):
    """The experiment configuration is invalid."""


class FeatureNotImplementedError(ConfigError):
    """A valid-looking configuration requests a future feature."""


class CandidateInvalid(ThreeBodyError):
    """A candidate violates the mandatory S0 contract."""


class RuleEvaluationError(ThreeBodyError):
    """A configured screening rule could not be evaluated."""


class SerializationError(ThreeBodyError):
    """An artifact could not be serialized safely."""


class ThermalExecutionError(ThreeBodyError):
    """A radiation or thermal solve failed to produce a valid result."""

