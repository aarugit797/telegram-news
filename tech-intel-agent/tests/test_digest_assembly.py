"""
Locks the digest's assembled shape.

The format has changed repeatedly - sections in, sections out, headlines
to entity-plus-clause, arrows in and out - and each change was made by
editing a prompt and eyeballing one sample. These tests pin the parts
that are NOT the model's to decide: the numbering contract that
follow-up questions resolve against, the links, the HTML escaping that
makes parse_mode=HTML safe, and the fixed closing template.
"""
from types import SimpleNamespace

import pytest

from app.processor.message_composer import DIGEST_CLOSING, _assemble, order_for_digest


def signal(source="github", title="astral-sh/uv", url="https://github.com/astral-sh/uv",
           summary="", score=4.0):
    return SimpleNamespace(source=source, title=title, url=url, summary=summary,
                           composite_score=score)


def entry(entity="uv", clause="replaces pip with one binary"):
    return {"entity": entity, "clause": clause}


def test_closing_is_the_fixed_template_and_comes_last():
    """The closer is ours, not the model's - it must appear verbatim."""
    out = _assemble([signal()], [entry()])
    assert out.endswith(DIGEST_CLOSING)
    assert "Reply with a number" in out


def test_closing_appears_even_though_no_entry_contains_it():
    """
    Proves the closer is appended by the assembler rather than echoed
    from model output: the entries below mention nothing like it.
    """
    entries = [entry(clause="does a thing"), entry(entity="duckdb", clause="does another")]
    out = _assemble([signal(), signal(title="duckdb/duckdb", url="https://x/y")], entries)
    assert out.count(DIGEST_CLOSING) == 1


def test_no_closing_on_an_empty_digest():
    """An empty digest is empty - never a bare invitation with no items."""
    assert _assemble([], []) == ""


def test_numbering_runs_straight_through_across_sources():
    """
    The numbering is the contract notification_history_tool resolves
    against, so it must not restart when the source changes.
    """
    signals = [signal(source="github"), signal(source="arxiv", title="A paper", url="https://a/b"),
               signal(source="hackernews", title="A thread", url="https://c/d")]
    out = _assemble(signals, [entry(), entry(), entry()])
    for n in (1, 2, 3):
        assert f"{n}. <b>" in out


def test_urls_come_from_the_signal_not_the_model():
    """A model-invented url must never reach the reader."""
    out = _assemble([signal(url="https://real.example/repo")],
                    [{"entity": "uv", "clause": "see https://hallucinated.example"}])
    assert "https://real.example/repo" in out


def test_model_written_text_is_escaped():
    """
    Unescaped angle brackets would 400 the whole message, since the
    sender ships the digest with parse_mode=HTML.
    """
    out = _assemble([signal()], [{"entity": "a<b>c", "clause": "x & y <script>"}])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out
    # The assembler's own bold tags survive.
    assert out.count("<b>") == 1


def test_lowercase_project_names_are_preserved():
    """'uv' must not become 'Uv' - that is not the project's name."""
    out = _assemble([signal(title="astral-sh/uv")], [entry(entity="uv")])
    assert "<b>uv</b>" in out


def test_prose_title_still_gets_a_capital():
    """
    A lowercase word in a prose title is just English, so the entity is
    capitalised. This shipped wrong once as "merging eleven services".
    """
    out = _assemble([signal(source="hackernews", title="The cost of microservices at small scale",
                            url="https://h/n")],
                    [entry(entity="microservice costs", clause="merged eleven services into two")])
    assert "<b>Microservice costs</b>" in out


def test_trailing_full_stop_is_stripped_from_the_clause():
    """The clause is a fragment; a full stop means drift back to sentences."""
    out = _assemble([signal()], [entry(clause="replaces pip with one binary.")])
    assert "one binary\n" in out


@pytest.mark.parametrize("source", ["github", "arxiv", "blogs", "rss", "hackernews"])
def test_no_section_labels_are_printed(source):
    """Grouping still happens; the label is not shown."""
    out = _assemble([signal(source=source, title="x/y", url="https://x/y")], [entry()])
    first = out.splitlines()[0]
    assert first.startswith("1. <b>")


def test_ordering_groups_by_source_even_though_labels_are_hidden():
    """order_for_digest is what makes the hidden grouping real."""
    signals = [signal(source="hackernews", score=5.0), signal(source="github", score=1.0),
               signal(source="hackernews", score=4.0)]
    assert [s.source for s in order_for_digest(signals)] == ["github", "hackernews", "hackernews"]


# --------------------------------------------------------------------------
# Source-cap selection. Pure and deterministic, so it is worth pinning
# hard: the fill step is the part most likely to be "simplified" later by
# someone who reads the caps as reservations.
# --------------------------------------------------------------------------
from app.processor.batch_assembly import _select_with_source_caps


def rep(source, score):
    return SimpleNamespace(source=source, composite_score=score,
                           title=f"{source}-{score}", id=f"{source}-{score}")


def sources(selected):
    out = {}
    for s in selected:
        out[s.source] = out.get(s.source, 0) + 1
    return out


def test_one_source_cannot_crowd_out_the_digest():
    """
    The real failure this exists to stop: 5 GitHub repos and nothing
    else. With enough sources present to fill the digest from capped
    slots alone, GitHub is held to exactly its cap.
    """
    candidates = [rep("github", 5.0 - i * 0.1) for i in range(10)]
    candidates += [rep("arxiv", 3.9), rep("arxiv", 3.8)]
    candidates += [rep("blogs", 3.7), rep("blogs", 3.6)]
    candidates += [rep("rss", 3.5), rep("hackernews", 3.4)]
    picked = _select_with_source_caps(candidates, 5)
    assert len(picked) == 5
    assert sources(picked)["github"] == 2


def test_a_cap_is_exceeded_only_by_exactly_the_shortfall():
    """
    Three sources present (10 github, 2 arxiv, 3 blogs) offer just
    2+1+1 = 4 capped slots against a digest of 5, so the fill step must
    take one more. It takes the single highest-scoring leftover - GitHub
    - and stops there. GitHub reaches 3, never 4 or 5.
    """
    candidates = [rep("github", 5.0 - i * 0.1) for i in range(10)]
    candidates += [rep("arxiv", 3.9), rep("arxiv", 3.8)]
    candidates += [rep("blogs", 3.7), rep("blogs", 3.6), rep("blogs", 3.5)]
    picked = _select_with_source_caps(candidates, 5)
    assert len(picked) == 5
    assert sources(picked)["github"] == 3


def test_fill_step_may_exceed_a_source_cap_to_keep_the_digest_full():
    """
    Caps are maximums, not reservations. With only two sources present
    there are not enough capped slots, so the fill step must push past
    a cap rather than ship a short digest.
    """
    candidates = [rep("github", 5.0 - i * 0.1) for i in range(6)]
    candidates += [rep("blogs", 4.0), rep("blogs", 3.9)]
    picked = _select_with_source_caps(candidates, 5)
    assert len(picked) == 5
    assert sources(picked)["github"] > 2


def test_fewer_candidates_than_the_cap_sends_what_exists():
    candidates = [rep("github", 4.5), rep("arxiv", 4.2), rep("rss", 4.0)]
    picked = _select_with_source_caps(candidates, 5)
    assert len(picked) == 3


def test_result_is_ranked_by_score():
    candidates = [rep("github", 4.0), rep("arxiv", 4.9), rep("blogs", 4.5)]
    picked = _select_with_source_caps(candidates, 5)
    assert [s.composite_score for s in picked] == [4.9, 4.5, 4.0]


def test_highest_scoring_item_of_a_capped_source_is_the_one_kept():
    """Within a source the cap must take the best, not the first seen."""
    candidates = [rep("github", 5.0), rep("github", 4.9), rep("github", 1.0),
                  rep("arxiv", 4.0)]
    picked = _select_with_source_caps(candidates, 3)
    gh = sorted(s.composite_score for s in picked if s.source == "github")
    assert gh == [4.9, 5.0]


def test_unknown_source_enters_only_through_the_fill_step():
    """A source absent from the caps config has no reserved slot."""
    candidates = [rep("github", 4.0), rep("arxiv", 3.9), rep("mystery", 5.0)]
    picked = _select_with_source_caps(candidates, 3)
    assert len(picked) == 3
    assert any(s.source == "mystery" for s in picked)
