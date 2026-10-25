from __future__ import annotations

from datetime import timedelta

import pytest

from app.enforcement.permissions import PermissionRejected, args_digest, issue, verify
from app.schemas.common import utcnow

KEY = b"test-signing-key-0123456789abcdef"
BASE = dict(event_id="evt_1", agent_id="a1", tool_name="send_purchase_summary", operation="send",
            resource="procurement:outbox", parameters={"subject": "s", "body": "b"},
            destination="team@d3coy.local")


def _issue(**over):
    kw = {**BASE, **over}
    return issue(KEY, ttl_seconds=kw.pop("ttl_seconds", 60), **kw)


def _verify(perm, **over):
    kw = {k: BASE[k] for k in ("tool_name", "operation", "resource", "parameters", "destination")}
    kw.update(over)
    verify(KEY, perm, **kw)


def test_valid_permission_verifies():
    _verify(_issue())  # no exception


def test_tampered_signature_rejected():
    perm = _issue()
    bad = perm.__class__(**{**perm.to_dict(), "signature": "0" * 64})
    with pytest.raises(PermissionRejected, match="signature"):
        _verify(bad)


def test_wrong_key_rejected():
    perm = _issue()
    with pytest.raises(PermissionRejected, match="signature"):
        verify(b"another-key", perm, tool_name=BASE["tool_name"], operation=BASE["operation"],
               resource=BASE["resource"], parameters=BASE["parameters"], destination=BASE["destination"])


def test_expired_permission_rejected():
    perm = _issue(now=utcnow() - timedelta(minutes=5), ttl_seconds=60)
    with pytest.raises(PermissionRejected, match="expired"):
        _verify(perm)


@pytest.mark.parametrize("field,value", [("tool_name", "delete_customer"), ("operation", "delete"),
                                         ("resource", "customers:db")])
def test_action_binding_enforced(field, value):
    perm = _issue()
    with pytest.raises(PermissionRejected, match="does not cover this call"):
        _verify(perm, **{field: value})


def test_changed_arguments_rejected():
    perm = _issue()
    with pytest.raises(PermissionRejected, match="arguments differ"):
        _verify(perm, parameters={"subject": "s", "body": "DIFFERENT"})
    with pytest.raises(PermissionRejected, match="arguments differ"):
        _verify(perm, destination="attacker@evil.example")


def test_no_key_fails_closed():
    perm = _issue()
    with pytest.raises(PermissionRejected, match="no signing key"):
        verify(b"", perm, tool_name=BASE["tool_name"], operation=BASE["operation"], resource=BASE["resource"],
               parameters=BASE["parameters"], destination=BASE["destination"])


def test_args_digest_is_order_independent():
    assert args_digest({"a": 1, "b": 2}, None) == args_digest({"b": 2, "a": 1}, None)
    assert args_digest({"a": 1}, "x") != args_digest({"a": 1}, "y")
