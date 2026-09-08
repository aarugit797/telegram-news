import pytest
from unittest.mock import AsyncMock, patch

from app.filters.hybrid_filter import run_hybrid_filter, COMPOSITE_THRESHOLD
from app.core.llm_client import LLMResult


def _always_true(_):
    return True


def _always_false(_):
    return False


def _fake_llm_result(novelty: int, relevance: int, applicability: int) -> LLMResult:
    return LLMResult(
        content={
            "novelty": novelty, "relevance": relevance, "applicability": applicability,
            "justification": "test justification",
        },
        input_tokens=10, output_tokens=10,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )


@pytest.mark.asyncio
async def test_rules_rejection_skips_llm_call():
    """A signal failing rules_check must never reach call_llm - zero LLM cost for the majority of rejected signals."""
    with patch("app.filters.hybrid_filter.call_llm", new_callable=AsyncMock) as mock_llm:
        result = await run_hybrid_filter(
            raw_data={"stars": 1}, rules_check=_always_false,
            system_prompt="sys", user_message="user", json_schema={}, trace_name="test",
        )

    assert result.passed is False
    assert result.stage_reached == "rejected_by_rules"
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_llm_rejection_below_threshold():
    """Rules pass, but a low composite LLM score should still reject the signal."""
    with patch("app.filters.hybrid_filter.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _fake_llm_result(novelty=2, relevance=2, applicability=2)
        result = await run_hybrid_filter(
            raw_data={}, rules_check=_always_true,
            system_prompt="sys", user_message="user", json_schema={}, trace_name="test",
        )

    assert result.passed is False
    assert result.stage_reached == "rejected_by_llm"
    assert result.composite_score == 2.0


@pytest.mark.asyncio
async def test_full_approval():
    """Rules pass AND the LLM score clears the threshold - signal should be approved."""
    with patch("app.filters.hybrid_filter.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _fake_llm_result(novelty=5, relevance=5, applicability=4)
        result = await run_hybrid_filter(
            raw_data={}, rules_check=_always_true,
            system_prompt="sys", user_message="user", json_schema={}, trace_name="test",
        )

    assert result.passed is True
    assert result.stage_reached == "passed"
    assert result.composite_score == pytest.approx(14 / 3)
    assert result.composite_score >= COMPOSITE_THRESHOLD
