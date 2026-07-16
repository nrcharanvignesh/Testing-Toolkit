"""Tests for core.network_status — lightweight API health tracker."""
from __future__ import annotations

import time
from unittest.mock import patch

import core.network_status as ns
from core.network_status import NetworkStatus, current_status, report_failure, report_success


def _reset() -> None:
    ns._last_success = 0.0
    ns._last_failure = 0.0


def test_initial_state_is_idle() -> None:
    _reset()
    assert current_status() == NetworkStatus.IDLE


def test_online_after_success() -> None:
    _reset()
    report_success()
    assert current_status() == NetworkStatus.ONLINE


def test_offline_after_failure() -> None:
    _reset()
    # Ensure failure timestamp is strictly after success
    ns._last_success = time.time() - 5.0
    report_failure()
    assert current_status() == NetworkStatus.OFFLINE


def test_online_after_success_following_failure() -> None:
    _reset()
    report_failure()
    report_success()
    assert current_status() == NetworkStatus.ONLINE


def test_idle_after_window_expires() -> None:
    _reset()
    ns._last_success = time.time() - 60.0
    assert current_status() == NetworkStatus.IDLE


def test_enum_values() -> None:
    assert NetworkStatus.ONLINE.value == "online"
    assert NetworkStatus.IDLE.value == "idle"
    assert NetworkStatus.OFFLINE.value == "offline"
