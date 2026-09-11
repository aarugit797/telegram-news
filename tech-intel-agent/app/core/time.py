"""
One source of "now" for the whole system.

datetime.utcnow() is a trap and was used everywhere here before this
module existed. Despite the name it returns a NAIVE datetime - the UTC
wall-clock reading with tzinfo stripped off - so the value carries no
record of which zone it belongs to. Two concrete consequences bit this
codebase:

  - Columns defaulted with it stored naive timestamps, while
    db/cleanup_job.py built its cutoffs with datetime.now(timezone.utc),
    which IS aware. asyncpg rejects an aware datetime bound to a
    `timestamp without time zone` parameter, so the weekly cleanup job
    could not run at all.

  - Nothing in the database recorded that the stored values were UTC.
    Any later reader had to know it by convention.

It is also deprecated from Python 3.12 onward.

Everything that needs the current time calls utcnow() from here, and
every timestamp column is `DateTime(timezone=True)` (Postgres
`timestamptz`), so the zone is carried end to end.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time, timezone-aware. Use instead of datetime.utcnow()."""
    return datetime.now(timezone.utc)
