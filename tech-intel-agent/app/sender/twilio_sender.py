"""
STUB - to be built.

WHAT: The sender worker - pulls messages from delivery_queue.py at
a controlled rate (10/sec, 2-sec delay between messages per user so
they arrive as separate WhatsApp bubbles), sends via
core/twilio_client.py, and logs delivery status back to the News DB
Batches table.

WHY: Enforces Twilio's rate limits and the "proactive notifications
need approved templates" compliance rule we identified. Retries
failed sends twice with 30-second backoff before logging failure
and firing a Sentry alert.

INPUT: Nothing external - continuously drains delivery_queue.py.

OUTPUT: Nothing returned - sends real WhatsApp messages as a side
effect, updates delivery_status in News DB.

CONNECTS TO: Reads from sender/delivery_queue.py. Uses
core/twilio_client.py. Writes delivery status via
db/repository_news.py. Reports failures to Sentry via
core/observability.py.
"""
