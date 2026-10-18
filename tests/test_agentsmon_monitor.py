"""Offline regression tests; no credentials, model or live services."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'monitoring' / 'agentsmon.py'


def module():
    spec = importlib.util.spec_from_file_location('haw_monitor', SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@contextmanager
def server(status=401, broken=False, sse=False):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, code, value, kind='application/json'):
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Mcp-Session-Id', 'fixture-session')
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            calls.append(('GET', self.headers.get('Authorization')))
            self.respond(status, b'secret-response-never-expose')
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((data['method'], self.headers.get('Mcp-Session-Id')))
            if data['method'] == 'notifications/initialized':
                self.respond(202, b''); return
            value = {'jsonrpc':'2.0', 'id':data['id'], 'result':
                     {'protocolVersion':'2024-11-05', 'capabilities':{}, 'serverInfo':{'name':'fixture','version':'1'}}
                     if data['method'] == 'initialize' else
                     {'tools': [{'name': n} for n in ('brain_search','brain_get','brain_neighbors')]}}
            if broken: value = {'jsonrpc':'2.0', 'id':data['id'], 'error':{'message':'SECRET'}}
            if sse:
                self.respond(200, b'event: message\ndata: ' + json.dumps(value).encode() + b'\n\n', 'text/event-stream')
            else: self.respond(200, value)
        def do_DELETE(self):
            calls.append(('DELETE', self.headers.get('Mcp-Session-Id')))
            self.respond(200, b'')
    srv = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{srv.server_port}', calls
    finally: srv.shutdown(); srv.server_close(); thread.join()


@pytest.mark.parametrize('status,expected', [(401,True),(200,False),(302,False),(403,False),(500,False)])
def test_anonymous_web_only_401_is_healthy(status, expected):
    m = module()
    with server(status=status) as (url,calls):
        row = m.web_check(url, 1)
    assert row['up'] is expected
    assert calls == [('GET', None)]
    assert 'secret' not in json.dumps(row).lower()


@pytest.mark.parametrize('sse', [False, True])
def test_mcp_real_protocol_and_closes_session(sse):
    m = module()
    with server(sse=sse) as (url,calls):
        assert m.mcp_check(url + '/mcp', 1)['up']
    assert [c[0] for c in calls] == ['initialize','notifications/initialized','tools/list','DELETE']
    assert calls[2][1] == 'fixture-session'


def test_mcp_http_200_error_not_healthy():
    m = module()
    with server(broken=True) as (url,calls):
        row = m.mcp_check(url + '/mcp', 1)
    assert not row['up']
    assert 'SECRET' not in json.dumps(row)
    assert calls[-1][0] == 'DELETE'


@pytest.mark.parametrize('url', ['https://example.org/mcp','http://user:pass@127.0.0.1:8',
                                 'file:///etc/passwd','http://localhost:8','http://127.0.0.1:8/?token=x'])
def test_only_literal_loopback_without_credentials(url):
    with pytest.raises(ValueError): module().endpoint(url)


def test_db_query_readonly(monkeypatch):
    m = module(); seen = {}
    class Conn:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql): seen['sql'] = sql; return self
        def fetchone(self): return (3, None)
    def connect(**kwargs): seen.update(kwargs); return Conn()
    import psycopg
    monkeypatch.setattr(psycopg, 'connect', connect)
    assert m.database_stats('fixture', 1) == (3, None)
    assert 'default_transaction_read_only=on' in seen['options']
    assert seen['sql'].lstrip().upper().startswith('SELECT')
    assert seen['dbname'] == 'fixture'
    assert seen['password'] == '' and seen['passfile'] == '/dev/null/no-passfile'
    assert seen['host'] == '/tmp'


def test_empty_index_explicit_optin_and_no_invented_time(monkeypatch):
    m = module()
    monkeypatch.setattr(m, 'database_stats', lambda *a:(0,None))
    assert m.db_checks('fixture', 1, allow_empty=True)[1]['up']
    assert not m.db_checks('fixture', 1, allow_empty=False)[1]['up']
    monkeypatch.setattr(m, 'database_stats', lambda *a:(2,None))
    assert not m.db_checks('fixture', 1, allow_empty=True)[1]['up']


def test_db_failure_not_zero_notes_and_secret_suppressed(monkeypatch):
    m = module()
    def fail(*a): raise RuntimeError('password=TOPSECRET')
    monkeypatch.setattr(m, 'database_stats', fail)
    rows = m.db_checks('fixture', 1)
    assert all(not r['up'] for r in rows)
    assert 'TOPSECRET' not in json.dumps(rows)
    assert '0 notes' not in json.dumps(rows)


def test_last_index_is_actual_database_timestamp(monkeypatch):
    from datetime import datetime, timezone
    m = module()
    monkeypatch.setattr(m, 'database_stats', lambda *a:(42,datetime(2026,1,2,3,4,tzinfo=timezone.utc)))
    result = m.db_checks('fixture', 1)[1]
    assert result['up'] and '42 notes' in result['detail']
    assert '2026-01-02 03:04 UTC' in result['detail']


def test_no_restarts_or_shell_from_monitor(monkeypatch):
    m = module(); calls = []
    def run(argv, **kwargs):
        calls.append((argv,kwargs))
        return subprocess.CompletedProcess(argv,0,'* * * * * /fixture/keeper.py\n','')
    monkeypatch.setattr(m.subprocess, 'run', run)
    assert m.keeper_check('/fixture/keeper.py')['up']
    assert calls[0][0] == ['/usr/bin/crontab','-l']
    assert not calls[0][1].get('shell')
    assert not m.keeper_check('/fixture/other.py')['up']


def test_malformed_config_is_loud_without_echo(tmp_path):
    path = tmp_path / 'config.json'; path.write_text('{"password":"TOPSECRET", bad}')
    result = subprocess.run([sys.executable, str(SCRIPT), '--config', str(path)], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['up'] is False
    assert 'TOPSECRET' not in result.stdout + result.stderr


def test_public_monitoring_source_is_english():
    # Includes failure paths and installer text, not only a healthy snapshot.
    forbidden = {chr(n) for n in (225,269,271,233,283,237,328,243,345,353,357,250,367,253,382)}
    forbidden |= {c.upper() for c in forbidden}
    for path in SCRIPT.parent.rglob('*'):
        if path.is_file() and path.suffix in ('.py', '.md', '.json'):
            assert not set(path.read_text()) & forbidden, path.name


def test_web_login_and_empty_index_have_english_labels(monkeypatch):
    m = module()
    with server() as (url, _):
        result = m.web_check(url, 1)
        assert result['name'] == 'Web login'
        assert result['detail'] == 'HTTP 401 · password required'
    monkeypatch.setattr(m, 'database_stats', lambda *a: (0, None))
    result = m.db_checks('fixture', 1, allow_empty=True)[1]
    assert result['name'] == 'Note index'
    assert result['detail'] == '0 notes · empty wiki allowed, no indexing yet'
