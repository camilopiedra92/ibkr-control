import pytest

from ibkr_control.db.guards import assert_single_process


def test_single_process_guard_passes_with_one_worker():
    assert_single_process({"WEB_CONCURRENCY": "1"}) is None
    assert_single_process({}) is None  # sin la var = default 1 worker


@pytest.mark.parametrize("var", ["WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"])
def test_single_process_guard_fails_with_multiple_workers(var):
    with pytest.raises(RuntimeError, match="1-proceso|in-process|SP5"):
        assert_single_process({var: "2"})
