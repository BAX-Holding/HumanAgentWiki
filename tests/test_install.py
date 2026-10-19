"""Execute installer sections without installing packages or changing the host."""
import os
from pathlib import Path
import subprocess

INSTALL = Path(__file__).resolve().parents[1] / "install.sh"


def section(start, end):
    return INSTALL.read_text().split(start, 1)[1].split(end, 1)[0]


def shell(code, tmp_path):
    full = INSTALL.read_text()
    if 'write_env() {' in full:
        code = 'write_env() {' + section('write_env() {', '# End configuration writer') + '\n' + code
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
    result = shell('HAW_PREPARE_ONLY=1; EXISTING_CONFIG=0; DIR=/fixture; ok(){ :; };\n' + guard
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
    result = shell(f'DIR="{tmp_path}"; EXISTING_CONFIG=0; HAW_NOTES_DIR="{notes}"; HAW_SEED_NOTES=N; '
                   'ask(){ echo Y; }; lc(){ printf "%s" "$1" | tr "[:upper:]" "[:lower:]"; }; '
                   'step(){ :; }; ok(){ :; };\n' + code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert (notes / ".git").is_dir()
    assert not (notes / "example.md").exists()


def test_rerun_preserves_embedding_model(tmp_path):
    (tmp_path / '.env').write_text('EMBED_MODEL=intfloat/multilingual-e5-small\nEMBED_DIM=384\n')
    code = section('# Pick the embedding model', '# 5b) swap')
    code = code[code.index('RAM_MB='):]
    result = shell('EXISTING_CONFIG=1; EMBED_MODEL=intfloat/multilingual-e5-small; EMBED_DIM=384; '
                   'uname(){ echo Darwin; }; sysctl(){ echo 17179869184; }; '
                   'ok(){ :; }; warn(){ :; };\n'+code, tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'EMBED_DIM=384' in (tmp_path/'.env').read_text()
    assert 'EMBED_MODEL=intfloat/multilingual-e5-small' in (tmp_path/'.env').read_text()


def test_schema_dimension_mismatch_stops_without_deleting_tables(tmp_path):
    (tmp_path/'.env').write_text('DATABASE_URL=dbname=fixture\nEMBED_DIM=1024\n')
    (tmp_path/'.venv/bin').mkdir(parents=True)
    py = tmp_path/'.venv/bin/python'
    py.write_text('#!/bin/sh\ncase "$*" in *DROP*) touch deleted;; esac\necho 384\n')
    py.chmod(0o700)
    code = section('# 6) schema', '# 7) notes folder')
    code = code[code.index('if command'):]
    result = shell('pg_isready(){ return 0; }; step(){ :; }; ok(){ :; }; warn(){ :; }; '
                   'die(){ echo "$*"; exit 1; }; load_env(){ export DATABASE_URL=dbname=fixture EMBED_DIM=1024; };\n'
                   +code, tmp_path)
    assert result.returncode != 0
    assert not (tmp_path/'deleted').exists()


def test_installer_env_loader_does_not_execute_values(tmp_path):
    (tmp_path/'.env').write_text('WEB_AUTH=reader:$(touch injected) with spaces; literal\n')
    full = INSTALL.read_text()
    assert 'load_env() {' in full
    loader = 'load_env() {' + section('load_env() {', '# End environment loader')
    result = shell(loader+'\nload_env\nprintf "%s" "$WEB_AUTH"', tmp_path)
    assert result.returncode == 0, result.stderr
    assert '$(touch injected)' in result.stdout
    assert not (tmp_path/'injected').exists()


def test_update_exits_before_agent_or_service_changes(tmp_path):
    guard = '# Headless preparation' + INSTALL.read_text().split('# 9) connect',1)[0].rsplit('# Headless preparation',1)[-1]
    (tmp_path/'.env').write_text('WEB_AUTH=reader:synthetic\n')
    result = shell('EXISTING_CONFIG=1; DIR=/fixture; ok(){ :; };\n'+guard+'\ntouch restarted',tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path/'restarted').exists()
    assert 'pkill -f' not in INSTALL.read_text()


def test_configuration_writer_preserves_special_characters(tmp_path):
    (tmp_path/'.env').write_text('WEB_AUTH=old\nOTHER=preserved\n')
    assert 'write_env() {' in INSTALL.read_text()
    writer = 'write_env() {' + section('write_env() {', '# End configuration writer')
    result = shell(writer+'\nwrite_env WEB_AUTH \'reader:a&b#c/$literal\'\n'
                   +'write_env NOTES_DIR \'/wiki/notes & drafts\'',tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path/'.env').read_text() == ('WEB_AUTH=reader:a&b#c/$literal\nOTHER=preserved\n'
                                           'NOTES_DIR=/wiki/notes & drafts\n')
    assert (tmp_path/'.env').stat().st_mode & 0o777 == 0o600


def test_interactive_auth_preserves_literal_password(tmp_path):
    (tmp_path/'.env').write_text('WEB_AUTH=old\n')
    full = INSTALL.read_text()
    helpers = ('write_env() {'+section('write_env() {','# End configuration writer')
               if 'write_env() {' in full else '')
    code=section('# 10) network reach', '# 12) (re)start')
    code=code[code.index('step "Web UI'):]
    result=shell(helpers+'\nEXISTING_CONFIG=0; step(){ :; }; warn(){ :; }; ok(){ :; }; '
                 'ask(){ case "$1" in *Protect*) printf y;; *username*) printf reader;; *) printf n;; esac; }; '
                 'ask_secret(){ printf \'a&b#c/$literal\'; }; '
                 'lc(){ printf "%s" "$1" | tr "[:upper:]" "[:lower:]"; };\n'+code,tmp_path)
    assert result.returncode==0,result.stderr
    assert 'WEB_AUTH=reader:a&b#c/$literal\n' in (tmp_path/'.env').read_text()


def test_interrupted_configuration_temp_cannot_be_added_to_git(tmp_path):
    (tmp_path/'.gitignore').write_text(INSTALL.with_name('.gitignore').read_text())
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    result=subprocess.run(['git','check-ignore','.env.ABC123'],cwd=tmp_path,capture_output=True)
    assert result.returncode==0
    example=subprocess.run(['git','check-ignore','.env.example'],cwd=tmp_path,capture_output=True)
    assert example.returncode==1
