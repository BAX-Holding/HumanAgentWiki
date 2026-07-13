import pytest

import server
import web
import index


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_brain_write_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "NOTES_DIR", str(tmp_path))

    with pytest.raises(Exception, match="escapes the notes directory"):
        server.brain_write(
            title="Unsafe",
            content="must never be written",
            category="Notes",
            file="../outside.md",
        )


def test_brain_write_creates_markdown_and_reindexes(tmp_path, monkeypatch):
    conn = _FakeConn()
    reindexed = []
    monkeypatch.setattr(web, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(index, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(web, "connect", lambda: conn)
    monkeypatch.setattr(web, "_git", lambda *args: None)
    monkeypatch.setattr(index, "reindex_files", lambda cur, paths: reindexed.extend(paths))

    result = server.brain_write(
        title="Shared fact",
        content="A durable fact for all agents.",
        category="Company",
        tags=["shared", "verified"],
    )

    expected = tmp_path / "Company" / "shared-fact.md"
    assert result == {"ok": True, "file": "Company/shared-fact.md", "created": True}
    assert expected.read_text(encoding="utf-8") == (
        "---\ntitle: Shared fact\ncategory: Company\n"
        "tags: [shared, verified]\n---\n\nA durable fact for all agents.\n"
    )
    assert reindexed == [str(expected)]
    assert conn.closed
    assert any("INSERT INTO files" in sql for sql, _ in conn.cur.executed)


def test_brain_write_refuses_existing_note_without_overwrite(tmp_path, monkeypatch):
    existing = tmp_path / "Company" / "existing.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("original", encoding="utf-8")
    conn = _FakeConn()
    monkeypatch.setattr(web, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(index, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(web, "connect", lambda: conn)
    monkeypatch.setattr(web, "_git", lambda *args: None)
    monkeypatch.setattr(index, "reindex_files", lambda cur, paths: None)

    with pytest.raises(FileExistsError, match="set overwrite=true"):
        server.brain_write(
            title="Existing",
            content="replacement",
            category="Company",
            file="Company/existing.md",
        )

    assert existing.read_text(encoding="utf-8") == "original"


def test_brain_write_rejects_symlink_escape(tmp_path, monkeypatch):
    notes = tmp_path / "notes"
    outside = tmp_path / "outside"
    notes.mkdir()
    outside.mkdir()
    (notes / "linked").symlink_to(outside, target_is_directory=True)
    conn = _FakeConn()
    monkeypatch.setattr(web, "NOTES_DIR", str(notes))
    monkeypatch.setattr(index, "NOTES_DIR", str(notes))
    monkeypatch.setattr(web, "connect", lambda: conn)
    monkeypatch.setattr(web, "_git", lambda *args: None)
    monkeypatch.setattr(index, "reindex_files", lambda cur, paths: None)

    with pytest.raises(Exception, match="escapes the notes directory"):
        server.brain_write(
            title="Escape",
            content="must stay inside notes",
            file="linked/escape.md",
            overwrite=True,
        )

    assert not (outside / "escape.md").exists()
