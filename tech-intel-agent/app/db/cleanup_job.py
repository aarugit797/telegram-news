"""
STUB - to be built.

WHAT: A weekly scheduled job - soft-deletes News DB signals older
than 30 days (sets is_deleted=True), hard-deletes anything older
than 90 days.

WHY: The News DB grows forever otherwise. Old signals from months
ago have no value for notifications or RAG search and just add
noise and storage cost.

INPUT: Nothing - runs on a schedule, reads current date.

OUTPUT: Nothing returned - deletes/soft-deletes rows as a side effect.

CONNECTS TO: Registered as a job in agents/scheduler.py, runs every
Sunday at 2am per our design. Uses repository_news.py to perform deletes.
"""
