#!/usr/bin/env python3
"""Publish tested assets without replacing tags or existing releases."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

plan = json.loads(Path('release-plan.json').read_text())
assert not plan['skip']
assert os.environ['GITHUB_REPOSITORY'] == 'csbxd/sing-box'
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == plan['source_sha']
repo = os.environ['GITHUB_REPOSITORY']
tag = plan['tag']
def verify_current_request():
    current = subprocess.check_output(['gh', 'api', f'repos/{repo}/git/ref/heads/custom-dev',
                                       '--jq', '.object.sha'], text=True).strip()
    if current != plan['request_commit']:
        raise RuntimeError('Branch changed after request; refusing stale publication')

verify_current_request()
# Refresh tags after long builds; fail closed on network errors or races.
remote = subprocess.check_output(['git', 'ls-remote', '--tags', 'origin', 'refs/tags/' + tag], text=True)
if remote.strip():
    raise RuntimeError('Tag already exists; refusing to replace it')
assets = sorted(Path('dist').glob('sing-box-*.tar.gz')) + sorted(Path('dist').glob('sing-box-*.zip'))
if len(assets) != 6:
    raise RuntimeError('Expected exactly six platform archives')
Path('dist/SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in assets))
Path('dist/source-metadata.json').write_text(json.dumps(plan, indent=2) + '\n')
body = (f"Custom build of `{plan['source_sha']}`.\n\n"
        f"Latest upstream Release in source version series: [{plan['upstream_release']}]({plan['upstream_url']}).\n\n"
        "Includes Linux, Windows and macOS amd64/arm64 pure-Go CLI archives. "
        "Android signed APKs are built in csbxd/sing-box-for-android.\n\n"
        '<!-- custom-core-release: ' + json.dumps(plan, separators=(',', ':')) + ' -->\n')
Path('release-notes.md').write_text(body)
args = ['gh', 'release', 'create', tag, '--repo', repo, '--target', plan['source_sha'],
        '--title', tag, '--notes-file', 'release-notes.md', '--draft', '--latest=false']
if plan['prerelease']:
    args.append('--prerelease')
subprocess.run(args + [str(p) for p in assets] + ['dist/SHA256SUMS', 'dist/source-metadata.json'], check=True)
verify_current_request()
subprocess.run(['gh', 'release', 'edit', tag, '--repo', repo, '--draft=false', '--latest=false'], check=True)
