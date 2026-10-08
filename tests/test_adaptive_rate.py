import json

from time_toolkit.adaptive_rate import AdaptiveRate


def test_probe_selects_measured_stable_rate_and_stops_at_throughput_plateau(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("time_toolkit.adaptive_rate.time.monotonic", lambda: now[0])
    control = AdaptiveRate(tmp_path / "rate.json", window=10)
    control.begin()
    for responses in (80, 80, 120, 160, 160, 160):
        control.sent = control.responses = responses
        now[0] += 10
        control._roll()
    assert control.mode == "holding"
    assert control.rate == 16
    assert control.best_throughput == 16
    assert json.loads(control.path.read_text())["best_rate"] == 16


def test_server_retry_after_stops_probe_and_preserves_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr("time_toolkit.adaptive_rate.time.monotonic", lambda: 100)
    control = AdaptiveRate(tmp_path / "rate.json")
    control.mode = "probing"
    control.rate = 24
    control.best_rate = 20
    control.response(429, "120")
    assert control.mode == "holding"
    assert control.rate == 12
    assert control.blocked_until == 220


def test_new_phase_can_probe_again_but_not_after_server_backoff(tmp_path):
    control = AdaptiveRate(tmp_path / "rate.json")
    control.begin("reads")
    control.mode = "holding"
    control.rate = 16
    control.begin("writes")
    assert control.phase == "writes" and control.mode == "warming"
    assert control.rate == 8
    control.response(429, "120")
    control.begin("another_phase")
    assert control.mode == "holding" and control.rate == 4


def test_network_failure_disables_further_probing(tmp_path):
    control = AdaptiveRate(tmp_path / "rate.json")
    control.begin("reads")
    control.network_failure()
    control.begin("writes")
    assert control.mode == "holding"
    assert control.last_backoff["status"] == "network"
