"""Execute installer sections without installing packages or changing the host."""
import os
from pathlib import Path
import subprocess
import pytest

INSTALL = Path(__file__).resolve().parents[1] / "install.sh"


def section(start, end):
    _, found_start, rest = INSTALL.read_text().partition(start)
    body, found_end, _ = rest.partition(end)
    if not found_start or not found_end:
        raise ValueError('Missing installer section boundary')
    return body


def shell(code, tmp_path):
    full = INSTALL.read_text()
    if 'write_env() {' in full:
        code = 'write_env() {' + section('write_env() {', '# End configuration writer') + '\n' + code
    return subprocess.run(["/bin/bash", "-c", "set -euo pipefail\n" + code],
                          cwd=tmp_path, capture_output=True, text=True,
                          start_new_session=True, timeout=10,
                          env={**os.environ, "USER": "fixture", "HOME": str(tmp_path),
                               "HAW_DIR": str(tmp_path / "fixture-checkout"),
                               "HAW_REPO": str(tmp_path / "missing-repository")})


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
    guard = section("# Headless preparation", "# 9) connect")
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
    # Missing delimiters must fail before executing any installer section.
    guard = '# Headless preparation' + section('# Headless preparation', '# 9) connect')
    (tmp_path/'.env').write_text('WEB_AUTH=reader:synthetic\n')
    result = shell('EXISTING_CONFIG=1; DIR=/fixture; ok(){ :; };\n'+guard+'\ntouch restarted',tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path/'restarted').exists()
    assert 'pkill -f' not in INSTALL.read_text()


def test_shell_fixture_cannot_use_the_real_home_or_remote(tmp_path):
    result = shell('printf "%s\\n" "$HOME" "$HAW_DIR" "$HAW_REPO"', tmp_path)
    assert result.returncode == 0
    assert result.stdout.splitlines() == [str(tmp_path), str(tmp_path / 'fixture-checkout'),
                                          str(tmp_path / 'missing-repository')]


@pytest.mark.parametrize('content', ['end marker only', 'start marker only'])
def test_section_rejects_either_missing_boundary(tmp_path, monkeypatch, content):
    fixture = tmp_path / 'installer.sh'
    fixture.write_text(content)
    monkeypatch.setitem(globals(), 'INSTALL', fixture)
    with pytest.raises(ValueError, match='Missing installer section boundary'):
        section('start marker', 'end marker')


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



def _restart_block(tmp_path, mcp_ready):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'calls.log'
    for name, body in (('agentsmon', f'echo "$*" >> {log}'), ('codex', 'true')):
        tool = bin_dir / name
        tool.write_text(f'#!/bin/bash\n{body}\nexit 0\n')
        tool.chmod(0o755)
    helpers = ('ok() { echo "OK $*"; }\nwarn() { echo "WARN $*"; }\n'
               'ask() { printf "%s" "$2"; }\nlc() { printf "%s" "$1" | tr "[:upper:]" "[:lower:]"; }\n'
               f'wait_for_mcp() {{ echo "wait $1" >> {log}; return {0 if mcp_ready else 1}; }}\n'
               'MCP_URL=http://127.0.0.1:8802/mcp\nDIR=/fixture\n')
    start = '  if command -v agentsmon >/dev/null 2>&1 && agentsmon restart --help'
    block = start + section(start, 'warn "Claude: restart its session to load the wiki (new sessions already have it)"\n  fi')
    block += 'warn "Claude: restart its session to load the wiki (new sessions already have it)"\n  fi\n'
    result = shell(f'export PATH="{bin_dir}:$PATH"\n' + helpers + block, tmp_path)
    assert result.returncode == 0, result.stderr
    return log.read_text().splitlines(), result.stdout


def test_agents_restart_only_after_the_mcp_answers(tmp_path):
    calls, out = _restart_block(tmp_path, mcp_ready=True)
    assert calls == ['restart --help', 'wait http://127.0.0.1:8802/mcp', 'restart --all']
    assert 'running agents restarted' in out


def test_no_restart_while_the_mcp_is_still_down(tmp_path):
    calls, out = _restart_block(tmp_path, mcp_ready=False)
    assert 'restart --all' not in calls
    assert 'MCP not answering yet' in out


def test_reboot_keeper_starts_missing_servers_once(tmp_path):
    work = tmp_path / 'wiki'
    (work / 'state').mkdir(parents=True)
    starts = tmp_path / 'starts.log'
    haw = work / 'haw'
    haw.write_text(f'#!/bin/bash\necho "$1" >> {starts}\nexec sleep 30\n')
    haw.chmod(0o755)
    cron = tmp_path / 'crontab.txt'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = bin_dir / 'crontab'
    fake.write_text(f'#!/bin/bash\nif [ "$1" = "-l" ]; then cat {cron} 2>/dev/null; else cat > {cron}; fi\n')
    fake.chmod(0o755)
    helpers = ('ok() { echo "OK $*"; }\nwarn() { echo "WARN $*"; }\n'
               'ask() { printf "%s" "$2"; }\nlc() { printf "%s" "$1" | tr "[:upper:]" "[:lower:]"; }\n'
               'uname() { echo Linux; }\n' f'DIR={work}\nMCP_URL=http://127.0.0.1:8802/mcp\ncd {work}\n')
    start = '# Keep MCP + web running after a reboot'
    block = start + section(start, '# done + link')
    result = shell(f'export PATH="{bin_dir}:$PATH"\n' + helpers + block + '\ncrontab -l\n', tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('# humanagentwiki-keep') == 2
    try:
        for _ in range(2):   # second run must not start duplicates
            subprocess.run(['/bin/bash', str(work / 'state' / 'keep.sh')], check=True, timeout=10)
            import time; time.sleep(0.5)
        assert sorted(starts.read_text().split()) == ['serve', 'web']
    finally:
        for svc in ('serve', 'web'):
            pid = (work / 'state' / f'{svc}.pid').read_text().strip()
            subprocess.run(['kill', pid])


def test_keeper_restarts_agents_after_boot_once_the_mcp_answers(tmp_path):
    work = tmp_path / 'wiki'
    (work / 'state').mkdir(parents=True)
    haw = work / 'haw'
    haw.write_text('#!/bin/bash\nexec sleep 30\n')
    haw.chmod(0o755)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    calls = tmp_path / 'agentsmon.log'
    for name, body in (('agentsmon', f'echo "$*" >> {calls}'), ('curl', 'printf 200'),
                       ('crontab', 'cat >/dev/null')):
        tool = bin_dir / name
        tool.write_text(f'#!/bin/bash\n{body}\n')
        tool.chmod(0o755)
    helpers = ('ok() { :; }\nwarn() { :; }\nask() { printf "%s" "$2"; }\n'
               'lc() { printf "%s" "$1" | tr "[:upper:]" "[:lower:]"; }\nuname() { echo Linux; }\n'
               f'DIR={work}\nMCP_URL=http://127.0.0.1:8802/mcp\ncd {work}\n')
    start = '# Keep MCP + web running after a reboot'
    block = start + section(start, '# done + link')
    assert shell(f'export PATH="{bin_dir}:$PATH"\n' + helpers + block, tmp_path).returncode == 0
    env = {**os.environ, 'PATH': f'{bin_dir}:' + os.environ['PATH'], 'HAW_UPTIME': '60'}
    try:
        subprocess.run(['/bin/bash', str(work / 'state' / 'keep.sh')], check=True, timeout=10, env=env)
        import time
        for _ in range(50):
            if calls.exists() and 'restart --all' in calls.read_text():
                break
            time.sleep(0.1)
        assert 'restart --all' in calls.read_text()
        calls.write_text('')
        subprocess.run(['/bin/bash', str(work / 'state' / 'keep.sh')], check=True, timeout=10, env=env)
        time.sleep(1)
        assert 'restart --all' not in calls.read_text()   # servers already up: no restart
    finally:
        for svc in ('serve', 'web'):
            pid = (work / 'state' / f'{svc}.pid').read_text().strip()
            subprocess.run(['kill', pid])


def _wait_for_mcp_fn():
    return 'wait_for_mcp() {' + section('wait_for_mcp() {', '\nload_env() {') + '\n'


def test_mcp_wait_stops_when_the_server_process_exited(tmp_path):
    (tmp_path / 'serve.pid').write_text('999999\n')   # no such process
    import time
    began = time.time()
    result = shell(_wait_for_mcp_fn() + 'if wait_for_mcp http://127.0.0.1:9/mcp 60 serve.pid; '
                   'then echo READY; else echo GAVE_UP; fi', tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'GAVE_UP' in result.stdout and 'process exited' in result.stdout
    assert time.time() - began < 5


def test_keeper_is_installed_before_agents_wait_for_the_mcp():
    text = INSTALL.read_text()
    assert text.index('# Keep MCP + web running after a reboot') < text.index('# Reload running agents')


def test_cpu_torch_index_only_on_linux_without_gpu():
    block = section('TORCH_INDEX=()', '.venv/bin/python -m pip install --progress-bar')
    assert 'nvidia-smi' in block and 'download.pytorch.org/whl/cpu' in block
    assert 'PIP_EXTRA_INDEX_URL' in block


def test_failed_swap_does_not_leave_the_file():
    block = section('step "Adding 4 GB swap', '# 6) schema')
    assert 'rm -f /swapfile' in block.split('else', 1)[1]
