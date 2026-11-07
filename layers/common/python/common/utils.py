import re
from datetime import datetime, timedelta, timezone


def format_date(date_str, input_format="%Y-%m-%d", output_format="%m/%d/%Y"):
    """Convert date from one format to another; None on bad input."""
    try:
        return datetime.strptime(date_str, input_format).strftime(output_format)
    except (ValueError, TypeError):
        return None


def extract_pattern(text, pattern):
    """Return the first regex match in text, or None."""
    if not text:
        return None
    match = re.search(pattern, text)
    return match.group(0) if match else None


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ttl_from_days(days):
    """Epoch seconds `days` from now, for DynamoDB TTL attributes."""
    return int((datetime.now(timezone.utc) + timedelta(days=int(days))).timestamp())


def dob_ms_to_iso(dob_ms):
    """IntakeQ DateOfBirth is a Unix timestamp in milliseconds; return YYYY-MM-DD or None."""
    if dob_ms in (None, '', 0):
        return None
    try:
        return datetime.fromtimestamp(int(dob_ms) / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def calculate_age_from_dob(dob_ms):
    """Age in whole years from an IntakeQ DateOfBirth (ms timestamp); None if invalid."""
    if dob_ms in (None, '', 0):
        return None
    try:
        birth = datetime.fromtimestamp(int(dob_ms) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        return None
    today = datetime.now(timezone.utc)
    age = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        age -= 1
    return age


def find_answer(questions, text_matches, contains=False):
    """
    Return the Answer of the first intake Question whose Text matches.

    text_matches: iterable of exact question texts (or substrings when
    contains=True). Matching is case-insensitive and whitespace-trimmed.
    """
    if not questions:
        return None
    wanted = [t.strip().lower() for t in text_matches]
    for question in questions:
        if not isinstance(question, dict):
            continue
        text = (question.get('Text') or '').strip().lower()
        answer = question.get('Answer')
        if answer in (None, ''):
            continue
        if contains:
            if any(w in text for w in wanted):
                return answer
        elif text in wanted:
            return answer
    return None


def clean_and_format_address(address_parts):
    """Join non-empty address components with commas."""
    if not address_parts:
        return ""
    return ", ".join(p for p in address_parts if p)
