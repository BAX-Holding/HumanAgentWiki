"""Execute installer sections without installing packages or changing the host."""
import os
from pathlib import Path
import subprocess

INSTALL = Path(__file__).resolve().parents[1] / "install.sh"


def section(start, end):
    return INSTALL.read_text().split(start, 1)[1].split(end, 1)[0]


def shell(code, tmp_path):
    return subprocess.run(["/bin/bash", "-c", "set -euo pipefail\n" + code],
                          cwd=tmp_path, capture_output=True, text=True,
                          start_new_session=True, env={**os.environ, "USER": "fixture"})


def test_macos_model_selection_without_free(tmp_path):
    (tmp_path / ".env").write_text("EMBED_MODEL=none\nEMBED_DIM=0\n")
    code = section("# Pick the embedding model", "# 5b) swap")
    code = code[code.index("RAM_MB="):]
    result = shell('uname(){ echo Darwin; }; sysctl(){ echo 17179869184; }; '
                   'free(){ return 127; }; ok(){ :; }; warn(){ :; };\n' + code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "EMBED_MODEL=BAAI/bge-m3" in (tmp_path / ".env").read_text()


def test_headless_prompt_has_no_tty_errors(tmp_path):
    code = section("ask() {", "lc()")
    result = shell("ask() {" + code + '\nask "prompt: " N', tmp_path)
    assert result.returncode == 0
    assert result.stdout == "N"
    assert result.stderr == ""


def test_macos_native_database_never_uses_sudo(tmp_path):
    code = section("# For a local/native Postgres:", "# 5) configuration")
    code = code[code.index("if pg_ready"):]
    (tmp_path / ".venv/bin").mkdir(parents=True)
    py = tmp_path / ".venv/bin/python"
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o700)
    result = shell('DB=fixture; DB_URL=dbname=fixture; pg_ready(){ return 0; }; '
                   'uname(){ echo Darwin; }; sudo(){ touch sudo-called; return 1; }; '
                   'createdb(){ :; }; ok(){ :; }; warn(){ :; };\n' + code, tmp_path)
    assert result.returncode == 0
    assert not (tmp_path / "sudo-called").exists()


def test_prepare_only_exits_before_any_agent_or_service_change(tmp_path):
    # Run the guard immediately preceding the agent-configuration section.
    full = INSTALL.read_text()
    guard = full.split("# 9) connect", 1)[0].rsplit("# Headless preparation", 1)[-1]
    assert "HAW_PREPARE_ONLY" in guard
    guard = "# Headless preparation" + guard
    (tmp_path / ".env").write_text("fixture")
    result = shell('HAW_PREPARE_ONLY=1; DIR=/fixture; ok(){ :; };\n' + guard
                   + '\ntouch unintended-start', tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "unintended-start").exists()


def test_notes_directory_and_no_seed_are_explicit(tmp_path):
    notes = tmp_path / "personal"
    (tmp_path / "sample_notes").mkdir()
    (tmp_path / "sample_notes/example.md").write_text("example")
    (tmp_path / ".env").write_text("NOTES_DIR=./notes\n")
    code = section("# 7) notes folder", "# 8) index")
    code = code[code.index('step "Notes'):]
    result = shell(f'DIR="{tmp_path}"; HAW_NOTES_DIR="{notes}"; HAW_SEED_NOTES=N; '
                   'ask(){ echo Y; }; lc(){ printf "%s" "$1" | tr "[:upper:]" "[:lower:]"; }; '
                   'step(){ :; }; ok(){ :; };\n' + code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert (notes / ".git").is_dir()
    assert not (notes / "example.md").exists()
