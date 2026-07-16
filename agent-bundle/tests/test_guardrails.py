"""Tests for core.guardrails module."""
from __future__ import annotations

import pytest

from core.guardrails import (
    REFUSAL_MESSAGE,
    _ALLOWED_PATTERN,
    _OFFTOPIC_PATTERNS,
    check_input_guardrail,
)


class TestCheckInputGuardrail:
    """Tests for the pre-filter guardrail function."""

    # -- Short messages pass through ------------------------------------------

    def test_short_message_passes(self) -> None:
        assert check_input_guardrail("hi") is None

    def test_empty_message_passes(self) -> None:
        assert check_input_guardrail("") is None

    def test_short_greeting_passes(self) -> None:
        assert check_input_guardrail("hello!") is None

    # -- Allowed keyword messages pass ----------------------------------------

    def test_test_keyword_passes(self) -> None:
        result = check_input_guardrail("How do I write a test case for login?")
        assert result is None

    def test_bug_keyword_passes(self) -> None:
        result = check_input_guardrail("I found a bug in the authentication module")
        assert result is None

    def test_sprint_keyword_passes(self) -> None:
        result = check_input_guardrail("What work items are in the current sprint backlog?")
        assert result is None

    def test_api_keyword_passes(self) -> None:
        result = check_input_guardrail("The API endpoint returns a 500 status code")
        assert result is None

    def test_automation_keyword_passes(self) -> None:
        result = check_input_guardrail("Set up a Playwright automation script for this flow")
        assert result is None

    # -- Off-topic messages are refused ---------------------------------------

    def test_weather_refused(self) -> None:
        result = check_input_guardrail("What is the weather forecast for tomorrow?")
        assert result == REFUSAL_MESSAGE

    def test_recipe_refused(self) -> None:
        result = check_input_guardrail("Give me a recipe for chocolate cake with ingredients")
        assert result == REFUSAL_MESSAGE

    def test_stocks_refused(self) -> None:
        result = check_input_guardrail("Should I invest in bitcoin or stocks right now?")
        assert result == REFUSAL_MESSAGE

    def test_movie_refused(self) -> None:
        result = check_input_guardrail("What are the best movies on Netflix this year?")
        assert result == REFUSAL_MESSAGE

    def test_leetcode_refused(self) -> None:
        result = check_input_guardrail("Solve this fibonacci dynamic programming problem")
        assert result == REFUSAL_MESSAGE

    def test_travel_refused(self) -> None:
        result = check_input_guardrail("Recommend hotels for my vacation in Hawaii")
        assert result == REFUSAL_MESSAGE

    def test_astrology_refused(self) -> None:
        result = check_input_guardrail("What does my horoscope say about my zodiac sign?")
        assert result == REFUSAL_MESSAGE

    def test_workout_refused(self) -> None:
        result = check_input_guardrail("Give me a workout plan for weight loss and calories")
        assert result == REFUSAL_MESSAGE

    # -- Mixed: off-topic word + allowed keyword passes -----------------------

    def test_weather_api_passes(self) -> None:
        result = check_input_guardrail("Write a test for the weather API endpoint")
        assert result is None

    def test_game_testing_passes(self) -> None:
        result = check_input_guardrail("Create test cases for the game login feature")
        assert result is None

    def test_crypto_security_test_passes(self) -> None:
        result = check_input_guardrail("Run security testing on the crypto payment endpoint")
        assert result is None

    # -- Long messages without keywords pass (let LLM decide) -----------------

    def test_long_ambiguous_message_passes(self) -> None:
        msg = (
            "I need to understand how the system handles concurrent "
            "connections under heavy load conditions and whether the "
            "architecture can sustain that throughput."
        )
        result = check_input_guardrail(msg)
        assert result is None

    # -- Case insensitivity ----------------------------------------------------

    def test_offtopic_case_insensitive(self) -> None:
        result = check_input_guardrail("Tell me about BITCOIN and CRYPTO trading")
        assert result == REFUSAL_MESSAGE

    def test_allowed_case_insensitive(self) -> None:
        result = check_input_guardrail("Run the REGRESSION TEST suite now")
        assert result is None


class TestAllowedPattern:
    """Verify the compiled regex matches expected keywords."""

    def test_multi_word_phrase_matches(self) -> None:
        assert _ALLOWED_PATTERN.search("check user story acceptance criteria")

    def test_no_match_on_random_text(self) -> None:
        assert _ALLOWED_PATTERN.search("the cat sat on the mat") is None


class TestOfftopicPatterns:
    """Verify off-topic patterns are compiled and functional."""

    def test_patterns_are_compiled(self) -> None:
        assert len(_OFFTOPIC_PATTERNS) > 0
        for pat in _OFFTOPIC_PATTERNS:
            assert hasattr(pat, "search")
