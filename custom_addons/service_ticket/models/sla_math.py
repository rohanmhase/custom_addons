# -*- coding: utf-8 -*-
"""Working-hours math. No Odoo imports -> unit-testable on its own.

Used by every SLA clock so all lanes share one implementation.
"""
from datetime import timedelta

_MAX_DAYS = 3660  # ~10 years: runaway guard for broken config


class WorkWindow(object):
    """A team's working window: start/end hour + which weekdays count."""

    __slots__ = ("start_sec", "end_sec", "days", "key")

    def __init__(self, start_hour=9.0, end_hour=19.0, days=None):
        self.start_sec = int(round(float(start_hour) * 3600))
        self.end_sec = int(round(float(end_hour) * 3600))
        if self.end_sec <= self.start_sec:
            self.start_sec, self.end_sec = 0, 86400  # broken config -> 24/7
        self.days = dict(days) if days else {i: True for i in range(7)}
        self.key = (self.start_sec, self.end_sec, tuple(sorted(self.days.items())))

    @classmethod
    def always_on(cls):
        return cls(0.0, 23.99, {i: True for i in range(7)})

    @classmethod
    def from_team(cls, team):
        if not team:
            return cls.always_on()
        flags = {
            0: team.work_mon, 1: team.work_tue, 2: team.work_wed, 3: team.work_thu,
            4: team.work_fri, 5: team.work_sat, 6: team.work_sun,
        }
        if not any(flags.values()):
            return cls.always_on()
        return cls(team.work_start_hour or 9.0, team.work_end_hour or 19.0, flags)

    def is_workday(self, dt):
        return bool(self.days.get(dt.weekday(), False))


def _start_of_day(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def add_working_seconds(start, seconds, window):
    """Datetime that is ``seconds`` of working time after ``start``."""
    remaining = float(seconds or 0)
    if remaining <= 0:
        return start
    day = _start_of_day(start)
    for _ in range(_MAX_DAYS):
        if window.is_workday(day):
            win_start = day + timedelta(seconds=window.start_sec)
            win_end = day + timedelta(seconds=window.end_sec)
            lo = start if start > win_start else win_start
            if lo < win_end:
                available = (win_end - lo).total_seconds()
                if remaining <= available:
                    return lo + timedelta(seconds=remaining)
                remaining -= available
        day += timedelta(days=1)
    return day + timedelta(seconds=remaining)


def working_seconds_between(start, end, window):
    """Working seconds available between two datetimes."""
    if not start or not end or end <= start:
        return 0.0
    total, day = 0.0, _start_of_day(start)
    for _ in range(_MAX_DAYS):
        if day >= end:
            break
        if window.is_workday(day):
            win_start = day + timedelta(seconds=window.start_sec)
            win_end = day + timedelta(seconds=window.end_sec)
            lo = start if start > win_start else win_start
            hi = end if end < win_end else win_end
            if hi > lo:
                total += (hi - lo).total_seconds()
        day += timedelta(days=1)
    return total
