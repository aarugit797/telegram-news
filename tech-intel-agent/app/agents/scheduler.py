"""
STUB - to be built.

WHAT: Sets up APScheduler and registers all 5 source agents plus the
weekly cleanup job, each on their designed cadence:
  - GitHub: every 2 hours
  - HackerNews: every 15 minutes
  - arXiv: once daily at 8am
  - AI Lab Blogs: every 30 minutes
  - RSS Newsletters: once daily at 9am
  - Cleanup job: weekly, Sunday 2am

WHY: Centralizes all scheduling in one place rather than each agent
managing its own timer - makes the full schedule visible and easy
to audit/change in one file.

INPUT: References to each agent's run function.

OUTPUT: A running AsyncIOScheduler instance.

CONNECTS TO: Started from pipeline_main.py. Registers functions from
all 5 agent files and db/cleanup_job.py.
"""
