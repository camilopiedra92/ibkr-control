"""In-memory stash for Step 3 XML uploads (per spec section 2 + D11)."""
import time

from ibkr_control.api._step3_stash import Step3Stash


def test_stash_put_and_get():
    stash = Step3Stash(ttl_seconds=3600)
    temp_id = stash.put(user_id=1, data={"hello": "world"}, sha256="abc")
    entry = stash.get(user_id=1, temp_id=temp_id)
    assert entry is not None
    assert entry.data == {"hello": "world"}
    assert entry.sha256 == "abc"


def test_stash_isolated_per_user():
    stash = Step3Stash(ttl_seconds=3600)
    tid_user1 = stash.put(user_id=1, data={"a": 1}, sha256="x")
    assert stash.get(user_id=2, temp_id=tid_user1) is None  # other user can't see


def test_stash_ttl_expiry():
    stash = Step3Stash(ttl_seconds=0.05)  # 50ms TTL
    tid = stash.put(user_id=1, data={"a": 1}, sha256="x")
    assert stash.get(user_id=1, temp_id=tid) is not None
    time.sleep(0.1)
    assert stash.get(user_id=1, temp_id=tid) is None


def test_stash_pop_removes_entry():
    stash = Step3Stash(ttl_seconds=3600)
    tid = stash.put(user_id=1, data={"a": 1}, sha256="x")
    popped = stash.pop(user_id=1, temp_id=tid)
    assert popped is not None
    assert stash.get(user_id=1, temp_id=tid) is None  # gone after pop


def test_stash_list_for_user_returns_non_expired_only():
    stash = Step3Stash(ttl_seconds=3600)
    stash.put(user_id=1, data={"a": 1}, sha256="x")
    stash.put(user_id=1, data={"b": 2}, sha256="y")
    stash.put(user_id=2, data={"c": 3}, sha256="z")
    user1_entries = stash.list_for_user(user_id=1)
    assert len(user1_entries) == 2
    assert {e.sha256 for e in user1_entries} == {"x", "y"}


def test_stash_find_by_sha256_dedup_check():
    """Used by step3/upload to detect dup against an already-stashed item."""
    stash = Step3Stash(ttl_seconds=3600)
    tid = stash.put(user_id=1, data={"a": 1}, sha256="abc")
    found = stash.find_by_sha256(user_id=1, sha256="abc")
    assert found is not None
    assert found.temp_id == tid
    assert stash.find_by_sha256(user_id=1, sha256="other") is None
