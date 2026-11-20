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


class TestFallbackPollInterval:
    BACKOFF = [1.0, 1.5, 2.25, 3.375, 5.0625, 7.59375, 10.0, 10.0]

    def test_backoff_grows_by_half_up_to_ten_seconds(self):
        interval = wait_poll.FALLBACK_POLL_INTERVAL
        sequence = []
        for _ in self.BACKOFF:
            sequence.append(interval)
            interval = wait_poll.next_fallback_poll_interval(interval)
        assert sequence == pytest.approx(self.BACKOFF)

    @pytest.mark.parametrize(("value", "jitter"), [(0.0, 0.8), (1.0, 1.2)])
    def test_fallback_interval_carries_the_jitter(self, stub_random, value, jitter):
        stub_random(value)
        intervals = [
            wait_poll.poll_interval(
                subscribed=True, event_backed=False, fast_poll_interval=0.1, fallback_poll_interval=step
            )
            for step in self.BACKOFF
        ]
        assert intervals == pytest.approx([step * jitter for step in self.BACKOFF])

    def test_event_backed_and_unsubscribed_ignore_the_fallback_interval(self, stub_random):
        stub_random(0.5)
        assert wait_poll.poll_interval(True, True, 0.1, fallback_poll_interval=7.5) == pytest.approx(5.0)
        assert wait_poll.poll_interval(False, False, 0.1, fallback_poll_interval=7.5) == 0.1


class TestRetryAfter:
    def test_seconds(self):
        assert wait_poll.retry_after_seconds({"Retry-After": "3"}) == 3.0
        assert wait_poll.retry_after_seconds({"retry-after": " 2.5 "}) == 2.5

    def test_http_date(self):
        from datetime import datetime, timezone

        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert wait_poll.retry_after_seconds({"Retry-After": "Thu, 01 Jan 2026 12:00:07 GMT"}, now=now) == 7.0
        assert wait_poll.retry_after_seconds({"Retry-After": "Thu, 01 Jan 2026 11:59:00 GMT"}, now=now) == 0.0

    @pytest.mark.parametrize("headers", [{}, {"Retry-After": "soon"}, {"Retry-After": "-4"}, {"Other": "3"}])
    def test_missing_or_unusable(self, headers):
        assert wait_poll.retry_after_seconds(headers) == 0.0
