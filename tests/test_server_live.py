"""Starting and stopping a live session through the hub the browser talks to.

The session itself is covered by tests/test_live_session.py. What matters here is the
control surface: one run at a time, a stop that is a request rather than a kill, and a
failure that reaches the page instead of leaving it on "starting" forever.
"""

import time

from flyvsly.server import RunHub


class Alive:
    def is_alive(self):
        return True


def test_one_run_at_a_time(tmp_path):
    hub = RunHub(tmp_path, "data")
    hub.thread = Alive()
    assert hub.start_live({}) == {"started": False, "reason": "A run is already in progress."}


def test_stopping_nothing_is_reported_not_raised(tmp_path):
    hub = RunHub(tmp_path, "data")
    assert hub.stop_live()["stopped"] is False


def test_a_failed_live_session_reaches_the_page(tmp_path):
    hub = RunHub(tmp_path, "data")
    seen = []
    hub.subscribe()  # a listener must exist, or the broadcast has nowhere to go
    assert hub.start_live({"engine": "not-an-engine"})["started"] is True
    deadline = time.time() + 20
    while hub.state.get("status") not in ("failed", "finished") and time.time() < deadline:
        time.sleep(0.05)
    assert hub.state["status"] == "failed"
    assert "not-an-engine" in hub.state["error"]
