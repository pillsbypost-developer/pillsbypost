"""common.utils: age/DOB math, intake answer lookup, TTL, date formatting."""
import time
from datetime import datetime, timezone

from common.utils import (
    calculate_age_from_dob, dob_ms_to_iso, find_answer, ttl_from_days, format_date,
)


def to_ms(dt):
    """IntakeQ DateOfBirth is a Unix timestamp in milliseconds."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


class TestCalculateAge:
    def test_birthday_already_passed_this_year(self):
        today = datetime.now(timezone.utc)
        assert calculate_age_from_dob(to_ms(datetime(today.year - 25, 1, 1))) == 25

    def test_birthday_not_yet_reached_this_year(self):
        today = datetime.now(timezone.utc)
        expected = 25 if (today.month, today.day) >= (12, 31) else 24
        assert calculate_age_from_dob(to_ms(datetime(today.year - 25, 12, 31))) == expected

    def test_pre_1970_dob_negative_timestamp(self):
        today = datetime.now(timezone.utc)
        ms = to_ms(datetime(1965, 1, 1))
        assert ms < 0
        assert calculate_age_from_dob(ms) == today.year - 1965

    def test_missing_returns_none(self):
        assert calculate_age_from_dob(None) is None
        assert calculate_age_from_dob(0) is None
        assert calculate_age_from_dob('') is None

    def test_invalid_returns_none(self):
        assert calculate_age_from_dob('not-a-timestamp') is None


class TestDobMsToIso:
    def test_converts(self):
        assert dob_ms_to_iso(to_ms(datetime(1990, 6, 15))) == '1990-06-15'

    def test_invalid(self):
        assert dob_ms_to_iso(None) is None
        assert dob_ms_to_iso('x') is None


class TestFindAnswer:
    QUESTIONS = [
        {'Text': 'Which pharmacy option?', 'Answer': 'Mail my pills'},
        {'Text': 'Amount', 'Answer': '150'},
        {'Text': 'Empty', 'Answer': ''},
    ]

    def test_exact_match_case_insensitive(self):
        assert find_answer(self.QUESTIONS, ['amount']) == '150'

    def test_contains(self):
        assert find_answer(self.QUESTIONS, ['pharmacy'], contains=True) == 'Mail my pills'

    def test_empty_answer_skipped_and_missing(self):
        assert find_answer(self.QUESTIONS, ['Empty']) is None
        assert find_answer(self.QUESTIONS, ['nope']) is None
        assert find_answer([], ['Amount']) is None
        assert find_answer(None, ['Amount']) is None


def test_ttl_from_days():
    ttl = ttl_from_days(10)
    assert abs(ttl - (time.time() + 10 * 86400)) < 5


class TestFormatDate:
    def test_default_formats(self):
        assert format_date('2026-07-03') == '07/03/2026'

    def test_invalid_returns_none(self):
        assert format_date('bogus') is None
        assert format_date(None) is None
