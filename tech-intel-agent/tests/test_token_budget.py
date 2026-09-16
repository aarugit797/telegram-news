"""
Pins the rolling summary's length guarantee.

A rolling summary is fed its own output every regeneration, so anything
that grows per pass grows without limit. The prompt asks for a length;
_cap is what enforces one, and it is pure, so it can be tested properly
rather than inferred from a live run.
"""
from app.responder.token_budget import SUMMARY_MAX_CHARS, _cap


def test_short_summary_is_untouched():
    text = "The reader works on embedded firmware in Rust."
    assert _cap(text) == text


def test_whitespace_is_normalised():
    assert _cap("two   spaces\nand a newline") == "two spaces and a newline"


def test_long_summary_is_capped():
    text = "This sentence is padded out. " * 200
    out = _cap(text)
    assert len(out) <= SUMMARY_MAX_CHARS


def test_cap_trims_at_a_sentence_boundary():
    """
    The capped text is fed back to the model as its own input next
    round. A fragment ending mid-clause invites it to finish the
    thought, which is how a bounded summary starts growing again.
    """
    text = "Alpha beta gamma. " * 200
    out = _cap(text)
    assert out.endswith(".")


def test_cap_falls_back_to_a_hard_cut_without_sentence_ends():
    """One enormous run-on must still be bounded."""
    text = "word " * 5000
    out = _cap(text)
    assert len(out) <= SUMMARY_MAX_CHARS
