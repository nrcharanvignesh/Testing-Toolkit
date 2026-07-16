"""Tests for ado.testcase_creator — pure logic and validation."""
from __future__ import annotations

from typing import Any

import pytest

from ado.testcase_creator import (
    normalize_category,
    _normalize_priority,
    normalize_payload,
    clean_title,
    validate_payload,
    _xml_encoded_step_html,
    build_steps_xml,
    ValidationReport,
    VALID_CATEGORIES,
    VALID_PRIORITIES,
)


# ---------------------------------------------------------------------------
# normalize_category
# ---------------------------------------------------------------------------


class TestNormalizeCategory:
    def test_exact_match_positive(self) -> None:
        assert normalize_category("Positive") == "Positive"

    def test_exact_match_case_insensitive(self) -> None:
        assert normalize_category("positive") == "Positive"
        assert normalize_category("NEGATIVE") == "Negative"
        assert normalize_category("Api Validation") == "API Validation"

    def test_alias_boundary(self) -> None:
        assert normalize_category("boundary") == "Data Validation"
        assert normalize_category("BVA") == "Data Validation"

    def test_alias_happy_path(self) -> None:
        assert normalize_category("happy path") == "Positive"
        assert normalize_category("functional") == "Positive"

    def test_alias_e2e(self) -> None:
        assert normalize_category("e2e") == "Integration"
        assert normalize_category("end to end") == "Integration"

    def test_alias_security(self) -> None:
        assert normalize_category("security") == "Negative"
        assert normalize_category("authorization") == "Negative"

    def test_alias_gui(self) -> None:
        assert normalize_category("ui") == "GUI Validation"
        assert normalize_category("ux") == "GUI Validation"

    def test_alias_performance(self) -> None:
        assert normalize_category("load") == "Performance"
        assert normalize_category("stress") == "Performance"

    def test_alias_accessibility(self) -> None:
        assert normalize_category("a11y") == "Accessibility"
        assert normalize_category("wcag") == "Accessibility"

    def test_unrecognized_falls_to_positive(self) -> None:
        assert normalize_category("completely unknown") == "Positive"
        assert normalize_category("xyz") == "Positive"

    def test_non_string_falls_to_positive(self) -> None:
        assert normalize_category(None) == "Positive"
        assert normalize_category(123) == "Positive"
        assert normalize_category([]) == "Positive"

    def test_whitespace_stripped(self) -> None:
        assert normalize_category("  Positive  ") == "Positive"
        assert normalize_category("  boundary  ") == "Data Validation"

    def test_all_valid_categories_round_trip(self) -> None:
        for cat in VALID_CATEGORIES:
            assert normalize_category(cat) == cat


# ---------------------------------------------------------------------------
# _normalize_priority
# ---------------------------------------------------------------------------


class TestNormalizePriority:
    def test_none_returns_none(self) -> None:
        assert _normalize_priority(None) is None

    def test_non_string_returns_medium(self) -> None:
        assert _normalize_priority(123) == "Medium"
        assert _normalize_priority([]) == "Medium"

    def test_exact_match(self) -> None:
        assert _normalize_priority("High") == "High"
        assert _normalize_priority("Low") == "Low"
        assert _normalize_priority("Medium") == "Medium"
        assert _normalize_priority("Lowest") == "Lowest"

    def test_case_insensitive(self) -> None:
        assert _normalize_priority("high") == "High"
        assert _normalize_priority("LOW") == "Low"

    def test_critical_maps_to_high(self) -> None:
        assert _normalize_priority("critical") == "High"
        assert _normalize_priority("highest") == "High"

    def test_numeric_priority_codes(self) -> None:
        assert _normalize_priority("1") == "High"
        assert _normalize_priority("2") == "High"
        assert _normalize_priority("3") == "Medium"
        assert _normalize_priority("4") == "Low"

    def test_compound_labels(self) -> None:
        assert _normalize_priority("1 - critical") == "High"
        assert _normalize_priority("2 - high") == "High"
        assert _normalize_priority("3 - medium") == "Medium"
        assert _normalize_priority("4 - low") == "Low"

    def test_unrecognized_defaults_medium(self) -> None:
        assert _normalize_priority("unknown") == "Medium"
        assert _normalize_priority("extreme") == "Medium"


# ---------------------------------------------------------------------------
# normalize_payload
# ---------------------------------------------------------------------------


class TestNormalizePayload:
    def _make_payload(self, category: str = "Positive", priority: str | None = None) -> dict:
        tc: dict[str, Any] = {"title": "t", "category": category, "steps": []}
        if priority is not None:
            tc["priority"] = priority
        return {"stories": [{"parent_work_item_id": 1, "test_cases": [tc]}]}

    def test_normalizes_category(self) -> None:
        data = self._make_payload(category="boundary")
        normalize_payload(data)
        assert data["stories"][0]["test_cases"][0]["category"] == "Data Validation"

    def test_normalizes_priority(self) -> None:
        data = self._make_payload(priority="critical")
        normalize_payload(data)
        assert data["stories"][0]["test_cases"][0]["priority"] == "High"

    def test_removes_none_priority(self) -> None:
        data = self._make_payload()
        data["stories"][0]["test_cases"][0]["priority"] = None
        normalize_payload(data)
        assert "priority" not in data["stories"][0]["test_cases"][0]

    def test_non_dict_input_safe(self) -> None:
        assert normalize_payload("bad") == "bad"
        assert normalize_payload(None) is None

    def test_missing_stories_safe(self) -> None:
        data: dict[str, Any] = {"other": 1}
        normalize_payload(data)
        assert data == {"other": 1}

    def test_non_list_stories_safe(self) -> None:
        data: dict[str, Any] = {"stories": "not a list"}
        normalize_payload(data)
        assert data["stories"] == "not a list"


# ---------------------------------------------------------------------------
# clean_title
# ---------------------------------------------------------------------------


class TestCleanTitle:
    def test_no_prefix(self) -> None:
        assert clean_title("Clean title") == "Clean title"

    def test_tc_prefix_stripped(self) -> None:
        assert clean_title("TC: Login test") == "Login test"
        assert clean_title("TC - Another test") == "Another test"

    def test_category_prefix_stripped(self) -> None:
        assert clean_title("Positive - SSO login with valid creds") == "SSO login with valid creds"
        assert clean_title("Data Validation - Check boundary") == "Check boundary"

    def test_tc_and_category_both_stripped(self) -> None:
        result = clean_title("TC: Data Validation - Closeout task trigger boundary")
        assert result == "Closeout task trigger boundary"

    def test_empty_returns_empty(self) -> None:
        assert clean_title("") == ""

    def test_only_prefix_returns_original(self) -> None:
        # If cleaning would leave empty, return original
        result = clean_title("TC:")
        assert result != ""

    def test_whitespace_handling(self) -> None:
        assert clean_title("  TC:  spaces  ") == "spaces"

    def test_preserves_non_matching_prefix(self) -> None:
        assert clean_title("FEAT: Something else") == "FEAT: Something else"


# ---------------------------------------------------------------------------
# validate_payload
# ---------------------------------------------------------------------------


class TestValidatePayload:
    def _valid_payload(self) -> dict:
        return {
            "schema_version": 1,
            "stories": [{
                "parent_work_item_id": 123,
                "test_cases": [{
                    "title": "Test login",
                    "category": "Positive",
                    "steps": [{"action": "Click login", "expected": "Page loads"}],
                }],
            }],
        }

    def test_valid_payload_passes(self) -> None:
        r = validate_payload(self._valid_payload())
        assert r.ok is True
        assert r.n_stories == 1
        assert r.n_test_cases == 1
        assert r.errors == []

    def test_non_dict_root(self) -> None:
        r = validate_payload("not a dict")
        assert r.ok is False
        assert "Root must be a JSON object" in r.errors[0]

    def test_missing_stories(self) -> None:
        r = validate_payload({"schema_version": 1})
        assert r.ok is False
        assert any("stories" in e for e in r.errors)

    def test_empty_stories(self) -> None:
        r = validate_payload({"schema_version": 1, "stories": []})
        assert r.ok is False

    def test_invalid_parent_id(self) -> None:
        data = self._valid_payload()
        data["stories"][0]["parent_work_item_id"] = -1
        r = validate_payload(data)
        assert r.ok is False
        assert any("parent_work_item_id" in e for e in r.errors)

    def test_missing_title(self) -> None:
        data = self._valid_payload()
        data["stories"][0]["test_cases"][0]["title"] = ""
        r = validate_payload(data)
        assert r.ok is False
        assert any("title" in e for e in r.errors)

    def test_invalid_category(self) -> None:
        data = self._valid_payload()
        data["stories"][0]["test_cases"][0]["category"] = "InvalidCat"
        r = validate_payload(data)
        assert r.ok is False
        assert any("category" in e for e in r.errors)

    def test_invalid_priority(self) -> None:
        data = self._valid_payload()
        data["stories"][0]["test_cases"][0]["priority"] = "SuperHigh"
        r = validate_payload(data)
        assert r.ok is False
        assert any("priority" in e for e in r.errors)

    def test_valid_priority_passes(self) -> None:
        data = self._valid_payload()
        data["stories"][0]["test_cases"][0]["priority"] = "High"
        r = validate_payload(data)
        assert r.ok is True

    def test_omitted_priority_passes(self) -> None:
        data = self._valid_payload()
        # No priority key at all
        r = validate_payload(data)
        assert r.ok is True

    def test_empty_steps(self) -> None:
        data = self._valid_payload()
        data["stories"][0]["test_cases"][0]["steps"] = []
        r = validate_payload(data)
        assert r.ok is False
        assert any("steps" in e for e in r.errors)

    def test_step_missing_action(self) -> None:
        data = self._valid_payload()
        data["stories"][0]["test_cases"][0]["steps"] = [
            {"action": "", "expected": "ok"}
        ]
        r = validate_payload(data)
        assert r.ok is False
        assert any("action" in e for e in r.errors)

    def test_step_missing_expected(self) -> None:
        data = self._valid_payload()
        data["stories"][0]["test_cases"][0]["steps"] = [
            {"action": "do thing"}
        ]
        r = validate_payload(data)
        assert r.ok is False
        assert any("expected" in e for e in r.errors)

    def test_schema_version_warning(self) -> None:
        data = self._valid_payload()
        data["schema_version"] = 2
        r = validate_payload(data)
        assert r.ok is True  # still valid, just warned
        assert len(r.warnings) > 0
        assert "schema_version" in r.warnings[0]

    def test_multiple_stories_counted(self) -> None:
        data = self._valid_payload()
        data["stories"].append({
            "parent_work_item_id": 456,
            "test_cases": [{
                "title": "Another",
                "category": "Negative",
                "steps": [{"action": "x", "expected": "y"}],
            }],
        })
        r = validate_payload(data)
        assert r.ok is True
        assert r.n_stories == 2
        assert r.n_test_cases == 2


# ---------------------------------------------------------------------------
# _xml_encoded_step_html
# ---------------------------------------------------------------------------


class TestXmlEncodedStepHtml:
    def test_plain_text(self) -> None:
        result = _xml_encoded_step_html("Hello world")
        # Must contain encoded <P> wrapper
        assert "&lt;P&gt;" in result
        assert "&lt;/P&gt;" in result
        assert "Hello world" in result

    def test_special_chars_double_encoded(self) -> None:
        result = _xml_encoded_step_html("if a > b")
        # The > becomes &gt; in HTML pass, then &amp;gt; in XML pass
        assert "&amp;gt;" in result

    def test_ampersand_encoded(self) -> None:
        result = _xml_encoded_step_html("A & B")
        # & -> &amp; (HTML) -> &amp;amp; (XML)
        assert "&amp;amp;" in result

    def test_less_than_encoded(self) -> None:
        result = _xml_encoded_step_html("x < y")
        assert "&amp;lt;" in result

    def test_newlines_become_br(self) -> None:
        result = _xml_encoded_step_html("line1\nline2")
        # <BR/> becomes &lt;BR/&gt; in the XML encoding
        assert "&lt;BR/&gt;" in result

    def test_empty_string(self) -> None:
        result = _xml_encoded_step_html("")
        assert "&lt;P&gt;&lt;/P&gt;" in result

    def test_none_treated_as_empty(self) -> None:
        result = _xml_encoded_step_html(None)  # type: ignore[arg-type]
        assert "&lt;P&gt;&lt;/P&gt;" in result


# ---------------------------------------------------------------------------
# build_steps_xml
# ---------------------------------------------------------------------------


class TestBuildStepsXml:
    def test_empty_steps(self) -> None:
        result = build_steps_xml([])
        assert result == '<steps id="0" last="1"></steps>'

    def test_single_step(self) -> None:
        steps = [{"action": "Click button", "expected": "Dialog opens"}]
        result = build_steps_xml(steps)
        assert '<steps id="0" last="2">' in result
        assert '<step id="2" type="ActionStep">' in result
        assert "</steps>" in result

    def test_multiple_steps_ids_start_at_2(self) -> None:
        steps = [
            {"action": "Step 1", "expected": "Result 1"},
            {"action": "Step 2", "expected": "Result 2"},
            {"action": "Step 3", "expected": "Result 3"},
        ]
        result = build_steps_xml(steps)
        assert '<steps id="0" last="4">' in result
        assert '<step id="2"' in result
        assert '<step id="3"' in result
        assert '<step id="4"' in result
        # No step id=1 (reserved by ADO)
        assert 'id="1"' not in result.replace('last="1"', '')

    def test_step_content_xml_encoded(self) -> None:
        steps = [{"action": "if a > b", "expected": "returns true"}]
        result = build_steps_xml(steps)
        # The action should be double-encoded (HTML then XML)
        assert "&amp;gt;" in result

    def test_missing_keys_handled(self) -> None:
        steps = [{}]  # no action or expected keys
        result = build_steps_xml(steps)
        # Should not raise; empty content
        assert '<step id="2"' in result


# ---------------------------------------------------------------------------
# ValidationReport dataclass
# ---------------------------------------------------------------------------


class TestValidationReport:
    def test_defaults(self) -> None:
        r = ValidationReport()
        assert r.ok is True
        assert r.errors == []
        assert r.warnings == []
        assert r.n_stories == 0
        assert r.n_test_cases == 0
