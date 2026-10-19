import importlib.util
import json
from pathlib import Path
import shlex
import stat


def test_registration_preserves_config_and_is_idempotent(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'monitoring/install_agentsmon.py'
    spec = importlib.util.spec_from_file_location('install_monitor',script)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    path = tmp_path/'config.json'
    old = {'dashboard':{'auth':{'pwhash':'SYNTHETIC'}},'services':[{'name':'Existing'}],
           'agents':[{'name':'Do not restart'}]}
    path.write_text(json.dumps(old))
    assert module.install(path,Path('/path with spaces/python'),tmp_path/'health.json')
    current = json.loads(path.read_text())
    assert current['dashboard'] == old['dashboard'] and current['agents'] == old['agents']
    assert current['services'][0] == old['services'][0]
    assert current['services'][1]['row_details'] is True
    assert shlex.split(current['services'][1]['command'])[0] == '/path with spaces/python'
    assert not module.install(path,Path('/path with spaces/python'),tmp_path/'health.json')
    backups = list(tmp_path.glob('*.bak-haw-*')); assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == old
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(backups[0].stat().st_mode) == 0o600
