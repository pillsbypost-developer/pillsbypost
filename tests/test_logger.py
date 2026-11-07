"""CustomLogger regression tests, notably the WARN alias (Python 3.13 removed Logger.warn)."""
import logging
from decimal import Decimal

from common.logger import CustomLogger


def test_warn_alias_does_not_crash_and_logs_warning(caplog):
    logger = CustomLogger()
    with caplog.at_level(logging.DEBUG, logger='common.logger'):
        logger.log('WARN', 'client not found')

    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == 'WARNING'
    assert 'client not found' in caplog.records[0].message


def test_warning_level_still_works(caplog):
    logger = CustomLogger()
    with caplog.at_level(logging.DEBUG, logger='common.logger'):
        logger.log('WARNING', 'explicit warning')

    assert caplog.records[0].levelname == 'WARNING'


def test_kwargs_serialized_including_decimal(caplog):
    logger = CustomLogger()
    with caplog.at_level(logging.DEBUG, logger='common.logger'):
        logger.log('INFO', 'processed', amount=Decimal('115'), state='CA')

    message = caplog.records[0].message
    assert 'processed' in message
    assert '115' in message
    assert 'CA' in message
