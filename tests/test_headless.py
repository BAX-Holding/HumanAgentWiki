from pathlib import Path
from unittest.mock import Mock

import pytest
import headless


def config(root, **extra):
    values = dict(MCP_HOST="127.0.0.1", WEB_HOST="192.0.2.1", WEB_AUTH="test:synthetic",
                  HF_HUB_OFFLINE="1")
    values.update(extra)
    (root / ".env").write_text("\n".join(f"{k}={v}" for k, v in values.items()))


@pytest.mark.parametrize("bad", [dict(WEB_AUTH=""), dict(WEB_AUTH="user:"),
                                 dict(MCP_HOST="192.0.2.1"), dict(HF_HUB_OFFLINE="0")])
def test_keeper_rejects_unsafe_environment(tmp_path, bad):
    config(tmp_path, **bad)
    with pytest.raises(ValueError):
        headless.environment(tmp_path)


def test_authenticated_lan_bind_is_explicitly_supported(tmp_path):
    config(tmp_path, WEB_HOST="0.0.0.0")
    assert headless.environment(tmp_path)["WEB_HOST"] == "0.0.0.0"


def test_keeper_boot_order_and_no_duplicate_start(tmp_path, monkeypatch):
    config(tmp_path)
    events = []
    def run(argv, **kw):
        events.append(argv)
        if argv[0] == "ps":
            mode = "serve" if argv[2] == "101" else "web"
            return Mock(returncode=0, stdout=f"/framework/Python.app/Contents/MacOS/Python {tmp_path}/cli.py {mode}\n")
        return Mock(returncode=3 if argv[-1] == "status" else 0)
    def popen(argv, **kw):
        events.append(argv)
        assert kw["env"]["HF_HUB_OFFLINE"] == "1"
        return Mock(pid=101 if argv[-1] == "serve" else 102)
    monkeypatch.setattr(headless.subprocess, "run", run)
    start = Mock(side_effect=popen)
    monkeypatch.setattr(headless.subprocess, "Popen", start)
    headless.ensure(tmp_path, Path("/pg/bin"), Path("/pg/data"))
    assert events[0][-1] == "status"
    assert events[1][-1] == "start"
    assert start.call_count == 2
    headless.ensure(tmp_path, Path("/pg/bin"), Path("/pg/data"))
    assert start.call_count == 2


def test_keeper_does_not_start_web_if_database_failed(tmp_path, monkeypatch):
    config(tmp_path)
    monkeypatch.setattr(headless.subprocess, "run", Mock(return_value=Mock(returncode=1)))
    start = Mock()
    monkeypatch.setattr(headless.subprocess, "Popen", start)
    with pytest.raises(RuntimeError):
        headless.ensure(tmp_path, Path("/pg/bin"), Path("/pg/data"))
    start.assert_not_called()


def test_reused_pid_for_other_process_is_not_healthy(tmp_path, monkeypatch):
    pid = tmp_path / "serve.pid"
    pid.write_text("101")
    monkeypatch.setattr(headless.subprocess, "run", Mock(return_value=Mock(
        returncode=0, stdout="python /other/wiki/cli.py serve\n")))
    assert not headless.alive(pid, ["python", str(tmp_path / "cli.py"), "serve"])
