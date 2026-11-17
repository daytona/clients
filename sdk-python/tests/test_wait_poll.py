# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from daytona.internal import wait_poll


@pytest.fixture
def stub_random(monkeypatch):
    def stub(value: float) -> None:
        monkeypatch.setattr(wait_poll, "random_unit", lambda: value)

    return stub


class TestPollInterval:
    def test_event_backed_wait_polls_every_five_seconds(self, stub_random):
        stub_random(0.5)
        assert wait_poll.poll_interval(subscribed=True, event_backed=True, fast_poll_interval=0.1) == pytest.approx(5.0)

    def test_subscribed_wait_without_connection_polls_every_second(self, stub_random):
        stub_random(0.5)
        assert wait_poll.poll_interval(subscribed=True, event_backed=False, fast_poll_interval=0.1) == pytest.approx(
            1.0
        )

    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
    @pytest.mark.parametrize("connected", [True, False])
    def test_unsubscribed_wait_keeps_the_fast_interval_without_jitter(self, stub_random, value, connected):
        stub_random(value)
        assert wait_poll.poll_interval(subscribed=False, event_backed=connected, fast_poll_interval=0.1) == 0.1
        assert wait_poll.poll_interval(subscribed=False, event_backed=connected, fast_poll_interval=0.42) == 0.42

    @pytest.mark.parametrize(
        ("value", "connected", "expected"),
        [
            (0.0, True, 4.0),
            (1.0, True, 6.0),
            (0.0, False, 0.8),
            (1.0, False, 1.2),
        ],
    )
    def test_jitter_bounds(self, stub_random, value, connected, expected):
        stub_random(value)
        assert wait_poll.poll_interval(
            subscribed=True, event_backed=connected, fast_poll_interval=0.1
        ) == pytest.approx(expected)

    def test_jitter_is_drawn_for_every_poll(self, monkeypatch):
        values = iter([0.0, 1.0])
        monkeypatch.setattr(wait_poll, "random_unit", lambda: next(values))
        first = wait_poll.poll_interval(subscribed=True, event_backed=True, fast_poll_interval=0.1)
        second = wait_poll.poll_interval(subscribed=True, event_backed=True, fast_poll_interval=0.1)
        assert (first, second) == (pytest.approx(4.0), pytest.approx(6.0))


class TestFastPollInterval:
    def test_fast_interval_grows_up_to_one_second(self):
        assert wait_poll.next_fast_poll_interval(0.1) == pytest.approx(0.11)
        assert wait_poll.next_fast_poll_interval(0.95) == 1.0
        assert wait_poll.next_fast_poll_interval(1.0) == 1.0
