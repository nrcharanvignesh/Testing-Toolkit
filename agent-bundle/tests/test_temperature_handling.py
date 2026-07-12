"""Regression tests for temperature handling in the LLM client.

Newer models (e.g. bedrock.anthropic.claude-opus-4-8) reject an explicit
`temperature` field with HTTP 400 "temperature is deprecated for this model".
The client must detect that, learn it for the session, and omit temperature so
generation succeeds instead of failing the whole run.
"""

import core.anthropic_client as ac


def _reset():
    ac._TEMPERATURE_UNSUPPORTED.clear()


def test_temperature_deprecation_detection():
    assert ac._is_temperature_deprecated(
        "`temperature` is deprecated for this model."
    )
    assert ac._is_temperature_deprecated("temperature is not supported")
    assert ac._is_temperature_deprecated("The temperature field cannot be set")
    # Unrelated 400s must NOT be treated as a temperature problem.
    assert not ac._is_temperature_deprecated("invalid api key")
    assert not ac._is_temperature_deprecated("max_tokens too large")


def test_wants_temperature_tracks_unsupported_models():
    _reset()
    model = "bedrock.anthropic.claude-opus-4-8"
    assert ac._wants_temperature(model) is True
    ac._TEMPERATURE_UNSUPPORTED.add(model)
    assert ac._wants_temperature(model) is False
    # A different model is unaffected.
    assert ac._wants_temperature("bedrock.anthropic.claude-sonnet-4-6") is True
    _reset()


def test_learning_is_idempotent():
    _reset()
    model = "bedrock.anthropic.claude-opus-4-8"
    ac._TEMPERATURE_UNSUPPORTED.add(model)
    ac._TEMPERATURE_UNSUPPORTED.add(model)
    assert len(ac._TEMPERATURE_UNSUPPORTED) == 1
    _reset()
