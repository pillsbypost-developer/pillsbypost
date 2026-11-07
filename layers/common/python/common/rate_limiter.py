import time
from threading import Lock

class RateLimiter:
    """
    Token bucket rate limiter for API calls

    Usage:
        limiter = RateLimiter(requests_per_minute=50)
        limiter.wait_if_needed()  # Blocks until safe to make request
        response = make_api_call()
    """

    def __init__(self, requests_per_minute):
        """
        Initialize rate limiter

        Args:
            requests_per_minute: Maximum requests allowed per minute (1-200)
        """
        if requests_per_minute < 1:
            requests_per_minute = 1
        elif requests_per_minute > 200:
            requests_per_minute = 200

        self.requests_per_minute = requests_per_minute
        self.min_interval = 60.0 / requests_per_minute  # Seconds between requests
        self.last_request_time = 0
        self.lock = Lock()

    def wait_if_needed(self):
        """
        Wait if necessary to respect rate limit

        This method blocks until it's safe to make another request
        """
        with self.lock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time

            if time_since_last < self.min_interval:
                sleep_time = self.min_interval - time_since_last
                time.sleep(sleep_time)

            self.last_request_time = time.time()

    def get_requests_per_minute(self):
        """Get configured requests per minute"""
        return self.requests_per_minute

    def get_min_interval(self):
        """Get minimum interval between requests in seconds"""
        return self.min_interval
