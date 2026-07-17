"""Tests for the domain clock seam (tools/clock.py).

The seam exists so eval references authored on a known date stay valid
(docs/DECISIONS.md §8). These pin the override, the unset fallback, the loud
failure on a malformed value, and the two consumers that read the seam.
"""

from datetime import date

import pytest

from tools import clock
from tools.fare_request import build_fare_request
from tools.policy import check_advance_purchase


def test_unset_returns_real_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(clock.ENV_VAR, raising=False)
    assert clock.today() == date.today()


def test_set_freezes_domain_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clock.ENV_VAR, "2026-07-07")
    assert clock.today() == date(2026, 7, 7)


def test_empty_value_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clock.ENV_VAR, "")
    assert clock.today() == date.today()


def test_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clock.ENV_VAR, "07/07/2026")
    with pytest.raises(ValueError):
        clock.today()


def test_check_advance_purchase_reads_the_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(clock.ENV_VAR, "2026-07-07")
    result = check_advance_purchase("2026-09-15")
    assert result["verdict"] == "pass"
    assert result["days_until_departure"] == 70


def test_fare_request_reads_the_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(clock.ENV_VAR, "2026-07-07")
    result = build_fare_request(
        origin="JFK",
        destination="LAX",
        departure_date="2026-09-15",
        travel_class="economy",
        passengers=[{"count": 1, "type": "adult"}],
        trip_type="one_way",
    )
    assert result["ok"] is True
    assert result["fare_request"]["fare_components"][0]["advance_purchase_days"] == 70


def test_explicit_today_param_beats_the_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(clock.ENV_VAR, "2026-07-07")
    result = build_fare_request(
        origin="JFK",
        destination="LAX",
        departure_date="2026-09-15",
        travel_class="economy",
        passengers=[{"count": 1, "type": "adult"}],
        trip_type="one_way",
        today="2026-09-01",
    )
    assert result["ok"] is True
    assert result["fare_request"]["fare_components"][0]["advance_purchase_days"] == 14
