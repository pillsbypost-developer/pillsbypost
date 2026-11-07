"""
Field validators for the generic CRUD handler. Each returns (is_valid, error).
"""
from decimal import Decimal
from typing import Tuple


def validate_non_empty_string(value) -> Tuple[bool, str]:
    if not isinstance(value, str):
        return False, "Must be a string"
    if len(value.strip()) == 0:
        return False, "Cannot be empty"
    return True, None


def validate_url(value) -> Tuple[bool, str]:
    if not isinstance(value, str):
        return False, "URL must be a string"
    if not value.startswith(('http://', 'https://')):
        return False, "URL must start with http:// or https://"
    return True, None


def validate_email(value) -> Tuple[bool, str]:
    if not isinstance(value, str):
        return False, "Email must be a string"
    if '@' not in value or '.' not in value:
        return False, "Invalid email format"
    return True, None


def validate_state_code(value) -> Tuple[bool, str]:
    """Two-letter US state/territory code."""
    if not isinstance(value, str):
        return False, "State code must be a string"
    if len(value.strip()) != 2 or not value.strip().isalpha():
        return False, "State code must be exactly 2 letters"
    return True, None


def validate_boolean(value) -> Tuple[bool, str]:
    if isinstance(value, bool):
        return True, None
    if isinstance(value, str) and value.strip().lower() in ('true', 'false'):
        return True, None
    return False, "Must be true or false"


def validate_non_negative_number(value) -> Tuple[bool, str]:
    try:
        if Decimal(str(value)) < 0:
            return False, "Must be zero or greater"
    except Exception:
        return False, "Must be a number"
    return True, None


def validate_pharmacy_choice(value) -> Tuple[bool, str]:
    """Payment tier pharmacy choice: 'mail' (ship / Nectar) or 'local'."""
    if value not in ('mail', 'local'):
        return False, "pharmacy_choice must be 'mail' or 'local'"
    return True, None


def validate_template_id(value) -> Tuple[bool, str]:
    return validate_non_empty_string(value)


def validate_template_content(value) -> Tuple[bool, str]:
    """Subject/body may be empty while a template is being drafted."""
    if not isinstance(value, str):
        return False, "Content must be a string"
    return True, None
