from __future__ import annotations


class ExternalFailure(Exception):  # noqa: N818 -- names a category, not one error
    """An outside system (a CLI tool, a service) failed; the message says how."""
