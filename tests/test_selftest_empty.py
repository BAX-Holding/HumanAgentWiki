from unittest.mock import Mock
import pytest
import selftest


@pytest.mark.parametrize("allow,files,rows,ok", [
    (True, [], 0, True), (False, [], 0, False),
    (True, ["unindexed.md"], 0, False), (True, [], 1, True)])
def test_empty_opt_in_does_not_hide_unindexed_notes(monkeypatch, allow, files, rows, ok):
    conn = Mock()
    conn.cursor.return_value.fetchone.return_value = (rows,)
    monkeypatch.setattr(selftest, "connect", lambda: conn)
    monkeypatch.setattr(selftest.index, "list_files", lambda: files)
    assert selftest.c_rowcount(allow_empty=allow)[0] is ok
