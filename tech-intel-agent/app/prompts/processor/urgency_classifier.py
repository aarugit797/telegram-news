"""
STUB - to be built.

WHAT: Prompt that classifies a signal as BREAKING or STANDARD.

WHY: BREAKING signals (major model releases, repos going viral in
under 2 hours) need to bypass the 30-minute batch window and send
immediately. STANDARD signals wait for the next scheduled batch.

CONNECTS TO: Used by processor/urgency_classifier.py.
"""
