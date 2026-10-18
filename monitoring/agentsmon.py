#!/usr/bin/env python3
"""Read-only Agentsmon service-card module. No dotenv/auth, model or note content.

Run with HAW's Python (psycopg) and an explicit non-secret JSON config. Recovery
belongs exclusively to the installation's existing keeper, never this probe.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import time
from urllib.parse import urlsplit

MAX_RESPONSE = 131072


def endpoint(url):
    p = urlsplit(url)
    if (p.scheme != 'http' or p.username or p.password or p.query or p.fragment
            or not ipaddress.ip_address(p.hostname).is_loopback):
        raise ValueError('Use a literal HTTP loopback endpoint without credentials')
    return p


def row(name, up, detail, started):
    return {'name': name, 'up': bool(up), 'detail': detail,
            'latency_ms': round((time.monotonic() - started) * 1000, 2)}


def web_check(url, timeout):
    started = time.monotonic(); conn = None
    try:
        p = endpoint(url)
        conn = http.client.HTTPConnection(p.hostname, p.port, timeout=timeout)
        # No Authorization, cookies, ambient proxy or redirect following.
        conn.request('GET', p.path or '/')
        status = conn.getresponse().status
        return row('Web login', status == 401,
                   'HTTP 401 · password required' if status == 401 else
                   f'HTTP {status} · expected 401 without authentication', started)
    except Exception:
        return row('Web login', False, 'Web unavailable or invalid endpoint', started)
    finally:
        if conn: conn.close()


def mcp_check(url, timeout):
    started = time.monotonic(); session = None
    deadline = started + timeout
    p = endpoint(url)

    def request(method, payload=None):
        nonlocal session
        left = deadline - time.monotonic()
        if left <= 0: raise TimeoutError('MCP deadline')
        conn = http.client.HTTPConnection(p.hostname, p.port, timeout=left)
        headers = {'Content-Type': 'application/json', 'Accept':'application/json, text/event-stream'}
        if session: headers['Mcp-Session-Id'] = session
        try:
            conn.request(method, p.path or '/', json.dumps(payload) if payload else None, headers)
            response = conn.getresponse()
            session = response.getheader('Mcp-Session-Id') or session
            if not 200 <= response.status < 300: raise ValueError('MCP HTTP error')
            if method == 'DELETE' or not payload.get('id'): return None
            if 'text/event-stream' in response.getheader('Content-Type', ''):
                size = 0
                while size <= MAX_RESPONSE and time.monotonic() < deadline:
                    # Bounded lines; never consume a persistent SSE stream to EOF.
                    conn.sock.settimeout(max(.01, deadline - time.monotonic())) if conn.sock else None
                    line = response.readline(MAX_RESPONSE + 1)
                    size += len(line)
                    if not line or size > MAX_RESPONSE: break
                    if line.startswith(b'data:'):
                        data = json.loads(line[5:]); break
                else: raise ValueError('MCP response limit')
                if 'data' not in locals(): raise ValueError('Missing MCP data')
            else:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE: raise ValueError('MCP response limit')
                data = json.loads(raw)
            if (not isinstance(data, dict) or data.get('id') != payload['id']
                    or 'error' in data or not isinstance(data.get('result'), dict)):
                raise ValueError('MCP RPC error')
            return data['result']
        finally: conn.close()

    try:
        initialized = request('POST', {'jsonrpc':'2.0', 'id':1, 'method':'initialize', 'params':{
            'protocolVersion':'2024-11-05', 'capabilities':{},
            'clientInfo':{'name':'haw-health', 'version':'1'}}})
        if not initialized.get('protocolVersion'): raise ValueError('MCP initialization error')
        request('POST', {'jsonrpc':'2.0','method':'notifications/initialized'})
        result = request('POST', {'jsonrpc':'2.0','id':2,'method':'tools/list'})
        names = {tool['name'] for tool in result['tools']}
        ok = {'brain_search','brain_get','brain_neighbors'} <= names
        return row('MCP', ok, 'Initialization + three read tools OK' if ok else
                   'Expected tools missing', started)
    except Exception:
        return row('MCP', False, 'Invalid or unavailable MCP protocol response', started)
    finally:
        if session:
            # Cleanup even after failure; no leaked session for every minute's probe.
            deadline = time.monotonic() + .5
            try: request('DELETE')
            except Exception: pass


def database_stats(database, timeout, socket_dir='/tmp', port=5432):
    import psycopg
    # No application .env or password file. Local OS-user/UNIX socket access only.
    # Explicit socket/port/OS user; no TCP or implicit PGHOST/PGUSER redirection.
    with psycopg.connect(dbname=database, host=socket_dir, port=port,
                         user=pwd.getpwuid(os.getuid()).pw_name, password='', passfile='/dev/null/no-passfile',
                         connect_timeout=max(1, int(timeout)),
                         options=f'-c default_transaction_read_only=on -c statement_timeout={int(timeout*1000)}') as conn:
        return conn.execute('SELECT count(*), max(updated_at) FROM files').fetchone()


def db_checks(database, timeout, allow_empty=False, socket_dir='/tmp', port=5432):
    started = time.monotonic()
    try:
        count, last = database_stats(database, timeout, socket_dir, port)
        good = isinstance(count, int) and count >= 0
        stamp = last.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if last else None
        detail = f'{count} notes · last index write: {stamp}' if stamp else (
            '0 notes · empty wiki allowed, no indexing yet' if count == 0 and allow_empty
            else 'Index timestamp missing or index empty')
        return [row('PostgreSQL', True, 'Read-only database query OK', started),
                row('Note index', good and ((count == 0 and allow_empty) or
                    (count > 0 and stamp is not None)), detail, started)]
    except Exception:
        # SQL/connection exceptions can contain DSN/passwords: never relay their text.
        return [row('PostgreSQL', False, 'Database or schema unavailable', started),
                row('Note index', False, 'Cannot verify note count or index timestamp', started)]


def keeper_check(marker):
    started = time.monotonic()
    try:
        result = subprocess.run(['/usr/bin/crontab','-l'], capture_output=True, text=True, timeout=2)
        ok = result.returncode == 0 and any(
            re.match(r'^\s*\*\s+\*\s+\*\s+\*\s+\*\s+', line) and marker in line
            for line in result.stdout.splitlines())
        return row('Recovery', ok, 'Existing keeper scheduled every minute' if ok else
                   'Cannot verify every-minute cron keeper', started)
    except Exception:
        return row('Recovery', False, 'Cannot verify cron', started)


def collect(settings):
    allowed = {'web_url','mcp_url','database','allow_empty','keeper_marker','timeout_seconds','db_socket','db_port'}
    if not isinstance(settings, dict) or set(settings) - allowed: raise ValueError('Invalid configuration')
    for key in ('web_url','mcp_url'): endpoint(settings[key])
    database = settings['database']; marker = settings['keeper_marker']
    if not isinstance(database, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,63}', database):
        raise ValueError('Invalid database name')
    if not isinstance(marker,str) or not marker or any(ord(c)<32 for c in marker):
        raise ValueError('Invalid keeper marker')
    empty = settings.get('allow_empty',False)
    if type(empty) is not bool: raise ValueError('Invalid empty policy')
    socket_dir = settings.get('db_socket','/tmp'); port = settings.get('db_port',5432)
    if not isinstance(socket_dir,str) or not socket_dir.startswith('/') or ',' in socket_dir:
        raise ValueError('Use a single local database socket directory')
    if type(port) is not int or not 1 <= port <= 65535: raise ValueError('Invalid database port')
    timeout = float(settings.get('timeout_seconds',3))
    if not 0.2 <= timeout <= 4: raise ValueError('Invalid timeout')
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        web = pool.submit(web_check, settings['web_url'], timeout)
        mcp = pool.submit(mcp_check, settings['mcp_url'], timeout)
        db = pool.submit(db_checks, database, timeout, empty, socket_dir, port)
        keeper = pool.submit(keeper_check, marker)
        rows = [web.result(), mcp.result(), *db.result(), keeper.result()]
    up = all(r['up'] for r in rows)
    return {'up':up, 'rows':rows, 'latency_ms':round((time.monotonic()-started)*1000,2),
            'detail':'All checks OK' if up else 'Failed: '+', '.join(r['name'] for r in rows if not r['up'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    try: result = collect(json.loads(args.config.read_text(encoding='utf-8')))
    except Exception: result = {'up':False,'detail':'Invalid configuration or module failure','rows':[]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['up'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
