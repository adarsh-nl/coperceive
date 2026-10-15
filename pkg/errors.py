# SPDX-License-Identifier: Apache-2.0
"""Errors for time queries and on-disk schema checks."""


class PkgError(Exception):
    """Base error for this library."""


class SchemaVersionError(PkgError):
    """The file's schema version is not the one this build reads."""


class TimeQueryError(PkgError):
    """A time query could not be answered."""


class ExtrapolationError(TimeQueryError):
    """The requested time is outside the recorded span.

    The library does not invent a pose past the last sample or before the first.
    """


class GapError(TimeQueryError):
    """The bracketing samples are farther apart than the caller allowed."""


class ToleranceError(TimeQueryError):
    """The chosen sample is farther from the query than the caller allowed."""
