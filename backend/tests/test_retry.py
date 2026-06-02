"""Tests del RetryPolicy + execute_with_retry helper (R5)."""

import pytest

from ibkr_control.ingest.retry import RetryPolicy, execute_with_retry


class _TransientError(Exception):
    pass


class _PermanentError(Exception):
    pass


@pytest.fixture
def fast_policy():
    """Policy con delays ~0 para tests rápidos."""
    return RetryPolicy(
        initial_delay_s=0.001,
        max_delay_s=0.01,
        multiplier=2,
        max_attempts=3,
        retryable_exceptions=(_TransientError,),
    )


@pytest.mark.asyncio
async def test_no_retry_when_fn_succeeds_first(fast_policy):
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        return "ok"

    result = await execute_with_retry(fn, fast_policy)

    assert result == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_once_then_succeed(fast_policy):
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _TransientError("first fails")
        return "ok"

    result = await execute_with_retry(fn, fast_policy)

    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_raises_last_exception_when_max_attempts_exceeded(fast_policy):
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _TransientError(f"call {calls}")

    with pytest.raises(_TransientError, match="call 3"):
        await execute_with_retry(fn, fast_policy)

    assert calls == 3


@pytest.mark.asyncio
async def test_non_retryable_exception_propagates_immediately(fast_policy):
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _PermanentError("nope")

    with pytest.raises(_PermanentError):
        await execute_with_retry(fn, fast_policy)

    assert calls == 1  # no retry


@pytest.mark.asyncio
async def test_predicate_blocks_retry(fast_policy):
    """Predicate=False → no retry even if exception class matches."""
    policy = RetryPolicy(
        initial_delay_s=0.001,
        max_delay_s=0.01,
        multiplier=2,
        max_attempts=3,
        retryable_exceptions=(_TransientError,),
        retryable_predicate=lambda exc: "retry_me" in str(exc),
    )

    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _TransientError("dont_retry")

    with pytest.raises(_TransientError):
        await execute_with_retry(fn, policy)

    assert calls == 1


@pytest.mark.asyncio
async def test_backoff_curve_is_exponential():
    """Verify delays follow initial * multiplier^n, capped at max_delay."""
    policy = RetryPolicy(
        initial_delay_s=1.0,
        max_delay_s=4.0,
        multiplier=2.0,
        max_attempts=5,
        retryable_exceptions=(_TransientError,),
    )

    delays = []

    async def fake_sleep(d):
        delays.append(d)

    async def fn():
        raise _TransientError("always fail")

    with pytest.raises(_TransientError):
        await execute_with_retry(fn, policy, _sleep=fake_sleep)

    # Attempts 1..5; sleep called between each pair (4 sleeps)
    # delays: [1.0, 2.0, 4.0 (capped), 4.0 (capped)]
    assert delays == [1.0, 2.0, 4.0, 4.0]


@pytest.mark.asyncio
async def test_on_retry_callback_invoked(fast_policy):
    callback_invocations = []

    async def fn():
        raise _TransientError("fail")

    def on_retry(exc, attempt, delay):
        callback_invocations.append((str(exc), attempt, delay))

    with pytest.raises(_TransientError):
        await execute_with_retry(fn, fast_policy, on_retry=on_retry)

    # max_attempts=3 → 2 retries → 2 callback invocations
    assert len(callback_invocations) == 2
    assert callback_invocations[0][1] == 1  # attempt number
    assert callback_invocations[1][1] == 2


def test_max_attempts_zero_raises_value_error():
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(
            initial_delay_s=0.001,
            max_delay_s=0.01,
            multiplier=2,
            max_attempts=0,
            retryable_exceptions=(_TransientError,),
        )


@pytest.mark.asyncio
async def test_max_attempts_one_raises_on_first_failure():
    """Boundary: max_attempts=1 means run once, no retries."""
    policy = RetryPolicy(
        initial_delay_s=0.001,
        max_delay_s=0.01,
        multiplier=2,
        max_attempts=1,
        retryable_exceptions=(_TransientError,),
    )
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _TransientError("only shot")

    with pytest.raises(_TransientError):
        await execute_with_retry(fn, policy)

    assert calls == 1
