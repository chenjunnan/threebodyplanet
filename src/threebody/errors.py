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


class VisualizationError(ThreeBodyError):
    """An M2 archive cannot be safely visualized."""

