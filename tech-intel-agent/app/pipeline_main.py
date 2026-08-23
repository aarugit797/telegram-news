"""
STUB - to be built.

WHAT: The entrypoint for Process One (Intelligence Pipeline).
Initializes observability, starts the APScheduler with all 5 agents
and the cleanup job registered, and keeps the process alive
indefinitely.

WHY: This is a completely separate long-running process from
Process Two (responder/main.py) - they never import from each other
directly, only communicate through the shared News DB and Redis.

INPUT: Nothing external - this IS the entrypoint, run directly via
`python -m app.pipeline_main`.

OUTPUT: A running process, alive forever until stopped.

CONNECTS TO: Calls core/observability.py at startup, then
agents/scheduler.py to register and start all scheduled jobs
(5 agents + batching_agent.py + cleanup_job.py).
"""
