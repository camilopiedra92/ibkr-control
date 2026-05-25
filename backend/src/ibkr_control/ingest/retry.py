"""RetryPolicy + execute_with_retry — backoff exponencial parametrizable.

HTTP-agnostic. Usable en cualquier async fn que pueda fallar transient.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Backoff exponencial parametrizable.

    Args:
        initial_delay_s: delay antes del primer retry, en segundos.
        max_delay_s: cap del delay (no crece más allá).
        multiplier: factor de crecimiento del delay entre retries.
        max_attempts: número total de intentos (incl. el primero). max_attempts=3
            significa 1 intento + 2 retries.
        retryable_exceptions: tuple de exception classes que disparan retry.
        retryable_predicate: extra check sobre la exception. Si devuelve False,
            no retry incluso si el class matchea retryable_exceptions.
    """
    initial_delay_s: float
    max_delay_s: float
    multiplier: float
    max_attempts: int
    retryable_exceptions: tuple[type[Exception], ...]
    retryable_predicate: Callable[[Exception], bool] | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(
                f"RetryPolicy.max_attempts must be >= 1, got {self.max_attempts}"
            )


async def execute_with_retry(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    on_retry: Callable[[Exception, int, float], None] | None = None,
    _sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """Ejecuta fn() con backoff. Lanza la última exception si max_attempts excedido.

    Args:
        fn: async callable que produce T o lanza exception.
        policy: RetryPolicy a aplicar.
        on_retry: callback opcional llamado antes de cada retry con
            (exception, attempt_number, delay_seconds). Útil para logging.
        _sleep: inyectable para tests (default asyncio.sleep). None = late-bind
            asyncio.sleep al momento de cada call para que monkeypatch propague.

    Returns:
        T si fn() succeeds en cualquier attempt.

    Raises:
        La última exception lanzada por fn() si max_attempts excedido,
        o cualquier exception non-retryable inmediatamente.
    """
    # Late-bind asyncio.sleep so that monkeypatch.setattr / patch("asyncio.sleep")
    # propagates correctly. If _sleep is provided explicitly (e.g. in tests via
    # _sleep=fake_sleep), use that instead.
    sleep_fn: Callable[[float], Awaitable[None]] = _sleep if _sleep is not None else asyncio.sleep

    delay = policy.initial_delay_s
    last_exc: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn()
        except policy.retryable_exceptions as exc:
            last_exc = exc

            # Predicate puede vetar el retry
            if policy.retryable_predicate is not None and not policy.retryable_predicate(exc):
                raise

            # Si fue el último attempt, propagar
            if attempt >= policy.max_attempts:
                raise

            if on_retry is not None:
                on_retry(exc, attempt, delay)

            await sleep_fn(delay)
            delay = min(delay * policy.multiplier, policy.max_delay_s)

    # Defensive: el loop nunca debería terminar sin return o raise
    assert last_exc is not None
    raise last_exc
