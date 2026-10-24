from __future__ import annotations


class ExternalFailure(Exception):  # noqa: N818 -- names a category, not one error
    """An outside system (a CLI tool, a service) failed; the message says how."""


class ConfigError(ValueError):
    """A configuration file is missing a value or holds one that cannot be used."""


class StateLockedError(RuntimeError):
    """Another run (the services or the one-process crawl) holds the shared state."""
