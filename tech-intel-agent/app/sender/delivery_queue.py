"""
STUB - to be built.

WHAT: A Redis-backed sub-queue holding composed messages waiting to
be sent, separate from the signal queue (DB 0).

WHY: Twilio has rate limits on outbound messages. Rather than sending
directly from the batching agent, messages are queued here and drained
by twilio_sender.py at a controlled rate (10 messages/second) -
decouples "deciding what to send" from "actually sending it".

INPUT: push_message(user_number, message_text, batch_id).

OUTPUT: pop_message() used by the sender worker.

CONNECTS TO: Written to by processor/batching_agent.py (via
message_composer.py output). Read by sender/twilio_sender.py.
"""
