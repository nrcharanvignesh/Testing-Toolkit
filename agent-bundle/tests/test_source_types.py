"""Tests for core.source_types and core.source_resolver."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.source_types import (
    SourceType,
    append_source_suffix,
    strip_source_suffix,
    SOURCE_SUFFIXES,
)


class TestStripSourceSuffix:
    def test_strips_ado_suffix(self) -> None:
        bare, src = strip_source_suffix("MyProject - ADO")
        assert bare == "MyProject"
        assert src == SourceType.ADO

    def test_strips_jira_suffix(self) -> None:
        bare, src = strip_source_suffix("MyProject - JIRA")
        assert bare == "MyProject"
        assert src == SourceType.JIRA

    def test_no_suffix_defaults_ado(self) -> None:
        bare, src = strip_source_suffix("MyProject")
        assert bare == "MyProject"
        assert src == SourceType.ADO

    def test_partial_suffix_not_stripped(self) -> None:
        bare, src = strip_source_suffix("MyProject - AD")
        assert bare == "MyProject - AD"
        assert src == SourceType.ADO

    def test_empty_string(self) -> None:
        bare, src = strip_source_suffix("")
        assert bare == ""
        assert src == SourceType.ADO


class TestAppendSourceSuffix:
    def test_appends_ado(self) -> None:
        assert append_source_suffix("Proj", SourceType.ADO) == "Proj - ADO"

    def test_appends_jira(self) -> None:
        assert append_source_suffix("Proj", SourceType.JIRA) == "Proj - JIRA"

    def test_roundtrip(self) -> None:
        for src in SourceType:
            name = "TestProject"
            suffixed = append_source_suffix(name, src)
            bare, detected = strip_source_suffix(suffixed)
            assert bare == name
            assert detected == src


class TestResolveSource:
    def test_explicit_jira_suffix(self) -> None:
        from core.source_resolver import resolve_source

        result = resolve_source("MyProject - JIRA")
        assert result == SourceType.JIRA

    def test_explicit_ado_suffix(self) -> None:
        from core.source_resolver import resolve_source

        result = resolve_source("MyProject - ADO")
        assert result == SourceType.ADO

    def test_unsuffixed_defaults_ado_when_both_configured(self) -> None:
        from core.source_resolver import resolve_source

        with patch("core.settings_store.is_jira_configured", return_value=True), \
             patch("core.settings_store.is_configured", return_value=True):
            result = resolve_source("MyProject")
            assert result == SourceType.ADO

    def test_unsuffixed_defaults_jira_when_only_jira(self) -> None:
        from core.source_resolver import resolve_source

        with patch("core.settings_store.is_jira_configured", return_value=True), \
             patch("core.settings_store.is_configured", return_value=False):
            result = resolve_source("MyProject")
            assert result == SourceType.JIRA

    def test_unsuffixed_defaults_ado_when_only_ado(self) -> None:
        from core.source_resolver import resolve_source

        with patch("core.settings_store.is_jira_configured", return_value=False), \
             patch("core.settings_store.is_configured", return_value=True):
            result = resolve_source("MyProject")
            assert result == SourceType.ADO


class TestResolvedSource:
    def test_resolve_raises_when_ado_not_configured(self) -> None:
        from core.source_resolver import resolve

        with patch("core.settings_store.load_pat_value", return_value=""), \
             patch("core.settings_store.get_setting", return_value=""), \
             patch("core.settings_store.is_jira_configured", return_value=False), \
             patch("core.settings_store.is_configured", return_value=False), \
             patch("core.settings_store.load_jira_pat", return_value=""):
            with pytest.raises(ValueError, match="PAT or organization"):
                resolve("MyProject")

    def test_resolve_raises_when_jira_not_configured(self) -> None:
        from core.source_resolver import resolve

        with patch("core.settings_store.is_jira_configured", return_value=False), \
             patch("core.settings_store.is_configured", return_value=False), \
             patch("core.settings_store.load_pat_value", return_value=""), \
             patch("core.settings_store.get_setting", return_value=""), \
             patch("core.settings_store.load_jira_pat", return_value=""):
            with pytest.raises(ValueError, match="JIRA is not configured"):
                resolve("MyProject - JIRA")
