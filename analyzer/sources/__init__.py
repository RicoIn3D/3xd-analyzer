"""Data sources. Each fetch function returns data on the analysis grid, or raises SourceUnavailable."""


class SourceUnavailable(Exception):
    """The source cannot be used in this run (missing key, no data, service error)."""
