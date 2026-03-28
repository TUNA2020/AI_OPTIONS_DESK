from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar


LOGGER = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])


def retry(attempts: int = 3, delay_seconds: float = 1.0, backoff: float = 2.0) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay_seconds
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt == attempts:
                        raise
                    LOGGER.warning(
                        "Retry %s/%s for %s due to: %s",
                        attempt,
                        attempts,
                        func.__name__,
                        exc,
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff

        return wrapper  # type: ignore[return-value]

    return decorator
