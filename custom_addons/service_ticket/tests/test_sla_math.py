# -*- coding: utf-8 -*-
"""Standalone parity test for the SLA math (no Odoo needed).

    python3 service_ticket/tests/test_sla_math.py
"""
import os
import random
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "models"))
from sla_math import WorkWindow, add_working_seconds, working_seconds_between  # noqa: E402


class FakeTeam(object):
    def __init__(self, start=9.0, end=19.0, flags=(1, 1, 1, 1, 1, 1, 0)):
        self.work_start_hour, self.work_end_hour = start, end
        (self.work_mon, self.work_tue, self.work_wed, self.work_thu,
         self.work_fri, self.work_sat, self.work_sun) = [bool(f) for f in flags]


def legacy_deadline(start_dt, seconds, team):
    """Port of the previous implementation, kept as the parity reference."""
    work_start = team.work_start_hour or 9.0
    work_end = team.work_end_hour or 19.0
    flags = {0: team.work_mon, 1: team.work_tue, 2: team.work_wed, 3: team.work_thu,
             4: team.work_fri, 5: team.work_sat, 6: team.work_sun}
    remaining, cursor = seconds, start_dt
    for _ in range(365 * 24):
        if remaining <= 0:
            break
        if not flags.get(cursor.weekday(), False):
            cursor = (cursor + timedelta(days=1)).replace(
                hour=int(work_start), minute=int((work_start % 1) * 60), second=0, microsecond=0)
            continue
        cur = cursor.hour * 3600 + cursor.minute * 60 + cursor.second
        ws, we = int(work_start * 3600), int(work_end * 3600)
        if cur >= we:
            cursor = (cursor + timedelta(days=1)).replace(
                hour=int(work_start), minute=int((work_start % 1) * 60), second=0, microsecond=0)
            continue
        if cur < ws:
            cursor = cursor.replace(hour=int(work_start), minute=int((work_start % 1) * 60),
                                    second=0, microsecond=0)
            cur = ws
        available = we - cur
        if remaining <= available:
            return cursor + timedelta(seconds=remaining)
        remaining -= available
        cursor = (cursor + timedelta(days=1)).replace(
            hour=int(work_start), minute=int((work_start % 1) * 60), second=0, microsecond=0)
    return cursor + timedelta(seconds=max(remaining, 0))


def test_parity():
    random.seed(11)
    teams = [
        FakeTeam(9, 19, (1, 1, 1, 1, 1, 1, 0)),
        FakeTeam(8.5, 17.5, (1, 1, 1, 1, 1, 0, 0)),
        FakeTeam(0.5, 23.5, (1, 1, 1, 1, 1, 1, 1)),
    ]
    cases = 0
    for team in teams:
        window = WorkWindow.from_team(team)
        for _ in range(300):
            start = datetime(2026, 1, 1) + timedelta(
                days=random.randint(0, 400), seconds=random.randint(0, 86399))
            secs = random.choice([60, 3600, 4 * 3600, 8 * 3600, 24 * 3600, 72 * 3600,
                                  7 * 86400, random.randint(1, 200000)])
            got = add_working_seconds(start, secs, window)
            want = legacy_deadline(start, secs, team)
            assert abs((got - want).total_seconds()) <= 1, (start, secs, got, want)
            cases += 1
    return cases


def test_pause_resume():
    window = WorkWindow.from_team(FakeTeam(9, 19, (1, 1, 1, 1, 1, 1, 0)))
    deadline = add_working_seconds(datetime(2026, 3, 2, 10, 0), 8 * 3600, window)
    left = working_seconds_between(datetime(2026, 3, 2, 13, 0), deadline, window)
    assert left == 5 * 3600, left
    resumed = add_working_seconds(datetime(2026, 3, 4, 15, 0), left, window)
    assert resumed == datetime(2026, 3, 5, 10, 0), resumed
    return left


def test_outside_hours_and_broken_config():
    window = WorkWindow.from_team(FakeTeam(9, 19, (1, 1, 1, 1, 1, 0, 0)))
    deadline = add_working_seconds(datetime(2026, 3, 6, 10, 0), 4 * 3600, window)
    assert deadline == datetime(2026, 3, 6, 14, 0), deadline
    assert working_seconds_between(datetime(2026, 3, 7, 12, 0), deadline, window) == 0
    broken = WorkWindow.from_team(FakeTeam(22, 6, (1, 1, 1, 1, 1, 1, 1)))
    got = add_working_seconds(datetime(2026, 3, 2, 10, 0), 8 * 3600, broken)
    assert got == datetime(2026, 3, 2, 18, 0), got
    return got


if __name__ == "__main__":
    print("parity OK (%d cases)" % test_parity())
    print("pause/resume OK, owed = %ss" % test_pause_resume())
    print("edge cases OK, fallback deadline = %s" % test_outside_hours_and_broken_config())
    print("ALL SLA MATH TESTS PASSED")
