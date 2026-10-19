#!/usr/bin/env python3
"""Register the service-card module; never start/restart any daemon or agent."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import tempfile


def install(config_path, python, monitor_config):
    if config_path.is_symlink(): raise ValueError('Use a regular Agentsmon config')
    config = json.loads(config_path.read_text())
    entry = {'name':'HumanAgentWiki', 'source':'module', 'timeout_seconds':12, 'row_details':True,
             'command':shlex.join([str(python), str(Path(__file__).with_name('agentsmon.py').resolve()),
                                  '--config', str(monitor_config)]), 'latency_label':'health checks'}
    services = config.setdefault('services',[])
    existing = [s for s in services if s.get('name') == entry['name']]
    if existing == [entry]: return False
    if len(existing) > 1: raise ValueError('Duplicate service name')
    if existing: services[services.index(existing[0])] = entry
    else: services.append(entry)
    # Preserve every other field, including credentials, without printing any.
    backup = config_path.with_name(config_path.name + '.bak-haw-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    with backup.open('xb') as target:
        os.fchmod(target.fileno(),0o600); target.write(config_path.read_bytes())
    fd, staged = tempfile.mkstemp(dir=config_path.parent, prefix='.haw-config-')
    try:
        with os.fdopen(fd,'w') as target:
            os.fchmod(target.fileno(),0o600)
            json.dump(config,target,ensure_ascii=False,indent=2); target.write('\n')
            target.flush(); os.fsync(target.fileno())
        os.replace(staged,config_path)
    finally:
        if os.path.exists(staged): os.unlink(staged)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agentsmon-config',required=True,type=Path)
    parser.add_argument('--python',required=True,type=Path)
    parser.add_argument('--monitor-config',required=True,type=Path)
    args = parser.parse_args()
    install(args.agentsmon_config.expanduser().absolute(),args.python.expanduser().absolute(),
            args.monitor_config.expanduser().resolve())
    print('HumanAgentWiki service registered; no process restarted.')
