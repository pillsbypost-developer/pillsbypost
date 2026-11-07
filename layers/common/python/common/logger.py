import json
import logging
import os

from common.dynamodb_helper import DecimalEncoder

class CustomLogger:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
        self.logger.setLevel(getattr(logging, log_level))

    def log(self, level, message, **kwargs):
        """Log a message with optional keyword arguments for structured logging"""
        log_level = level.upper()

        # Python 3.13 removed Logger.warn — accept WARN as an alias for WARNING
        if log_level == 'WARN':
            log_level = 'WARNING'

        # Skip verbose logs in production unless specifically set to DEBUG
        if os.environ.get('LOG_LEVEL', 'INFO').upper() == 'DEBUG' or (
            log_level != 'DEBUG' and
            not any(k for k in kwargs if k.lower() in ['intake_data', 'event', 'appointment_data'])
        ):
            log_method = getattr(self.logger, log_level.lower())

            # Format extra info as JSON if available
            extra_info = json.dumps(kwargs, cls=DecimalEncoder) if kwargs else ''
            log_message = f"{message}{f' - Extra Info: {extra_info}' if extra_info else ''}"

            log_method(log_message)
