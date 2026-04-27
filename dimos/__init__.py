"""dimos - A fork of dimensionalOS/dimos.

A framework for building and deploying robotic systems with
dimensional awareness and modular agent-based architectures.

Fork notes:
    Forked for personal learning and experimentation with agent architectures.
    Upstream: https://github.com/dimensionalOS/dimos

Personal changes:
    - Added __fork_author__ to track fork ownership separately from upstream contributors.
    - Added __fork_version__ to track personal fork versioning independently of upstream.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dimos")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"

__author__ = "dimos contributors"
__fork_author__ = "personal fork"
# Track personal fork version separately from upstream package version
__fork_version__ = "0.1.0"
__license__ = "Apache-2.0"
__upstream__ = "https://github.com/dimensionalOS/dimos"

__all__ = [
    "__version__",
    "__author__",
    "__fork_author__",
    "__fork_version__",
    "__license__",
    "__upstream__",
]
