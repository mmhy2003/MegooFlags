"""MegooFlags server SDK for Python: an OpenFeature provider that evaluates flags in-process."""

from megooflags._version import __version__
from megooflags.config import MegooFlagsConfig
from megooflags.provider import MegooFlagsProvider

__all__ = ["MegooFlagsConfig", "MegooFlagsProvider", "__version__"]
