"""Contract test: the Socket.IO events the server emits must reach the client.

This exists because of a real bug. The background price tracker emitted
``price_update`` on the ``/stream`` namespace while the browser connected to the
default namespace and listened there. Both sides looked correct in isolation, the
socket connected successfully, and the UI showed a healthy connection — but no tick
ever arrived, and prices only moved via the 60-second polling refresh.

Every one of the other 71 tests passed throughout, because they all exercise pure
functions in isolation. A mismatch in a contract *between* two components is
invisible to that style of test.

These tests read the server and client source directly rather than importing
``app.py``, which cannot currently be imported without initializing the database
and starting a background thread as an import side effect (see §9 of
docs/technical-report.md). A true runtime integration test is the better fix and
requires moving those side effects behind a factory function.
"""

import ast
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "Plotly dashboard", "app.py")
CLIENT = os.path.join(ROOT, "frontend", "dashboard.js")

DEFAULT_NS = "/"


def _emit_sites() -> list:
    """Every ``socketio.emit`` call site as (event, namespace, line).

    Deliberately a flat list of call sites rather than a map of event -> set of
    namespaces. The union view is what let the original bug through here: the
    same event was emitted correctly by a function nobody calls and incorrectly
    by the one that actually runs, and unioning the two namespaces made the
    broken call site invisible.

    A ``socketio.emit`` with no ``namespace=`` keyword targets the default
    namespace, which is what a client connecting without a path receives.
    """
    with open(SERVER, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "emit"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "socketio"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue

        namespace = DEFAULT_NS
        for kw in node.keywords:
            if kw.arg == "namespace" and isinstance(kw.value, ast.Constant):
                namespace = kw.value.value

        sites.append((node.args[0].value, namespace, node.lineno))
    return sites


def _client_namespace() -> str:
    """The namespace the browser actually connects to.

    ``io(url)`` connects to the default namespace unless the URL carries a path.
    """
    with open(CLIENT, encoding="utf-8") as fh:
        source = fh.read()

    match = re.search(r"WS:\s*[\"']([^\"']+)[\"']", source)
    if not match:
        pytest.fail("could not locate the WS endpoint in dashboard.js")

    path = re.sub(r"^https?://[^/]+", "", match.group(1))
    return path.rstrip("/") or DEFAULT_NS


def _client_subscriptions() -> set:
    """Event names the browser registers handlers for, excluding lifecycle events."""
    with open(CLIENT, encoding="utf-8") as fh:
        source = fh.read()

    events = set(re.findall(r"socket\.on\(\s*[\"']([^\"']+)[\"']", source))
    return events - {"connect", "disconnect", "connect_error", "reconnect"}


def test_client_subscribes_to_something():
    """Guard: if this breaks, the extraction below is silently testing nothing."""
    subs = _client_subscriptions()
    assert subs, "no socket.on() subscriptions found — did the client change shape?"
    assert "price_update" in subs


@pytest.mark.parametrize("event", sorted(_client_subscriptions()))
def test_subscribed_event_is_emitted_somewhere_the_client_listens(event):
    """Every event the client listens for must be emitted where the client is listening."""
    client_ns = _client_namespace()
    reachable = [s for s in _emit_sites() if s[0] == event and s[1] == client_ns]

    assert reachable, (
        f"client subscribes to {event!r} on namespace {client_ns!r} but no "
        f"server emit targets that namespace — the socket will connect "
        f"successfully and the event will never arrive."
    )


def test_no_emit_site_targets_a_namespace_no_client_is_on():
    """Each emit site is checked individually, so a working sibling cannot mask a broken one."""
    client_ns = _client_namespace()
    subscribed = _client_subscriptions()

    stranded = [
        f"{event!r} emitted on {ns!r} at app.py:{line}"
        for event, ns, line in _emit_sites()
        if event in subscribed and ns != client_ns
    ]
    assert not stranded, (
        "these emit sites push events the client subscribes to onto a namespace "
        f"it never connects to (client is on {client_ns!r}):\n  "
        + "\n  ".join(stranded)
    )
