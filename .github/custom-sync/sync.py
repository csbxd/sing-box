#!/usr/bin/env python3
"""Actual cherry-pick synchronization with metadata/patch audits and a lease."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

REPO = 'csbxd/sing-box'
BRANCH = 'custom-dev'
CONTROL = 'maintenance/custom-sync'
ORIGINAL_HEAD = '8eedb38da3d5a7ab6c06356210c93301ed55d733'
ORIGINALS = ['bdfede69ea4cba6f654dd19ad90c2cb62f465be6', ORIGINAL_HEAD]
CI_COMMIT = '61a73e57fa2ab7407b5352b65c6843778c8b78d4'
SHA = re.compile(r'[0-9a-f]{40}\Z')


def run(*args, cwd=None, data=None):
    return subprocess.check_output(args, cwd=cwd, input=data).strip()


def git(*args, cwd=None):
    return run('git', *args, cwd=cwd).decode()


def fingerprint(sha, cwd=None):
    entries = run('git', 'ls-tree', '-r', '-z', sha, cwd=cwd).split(b'\0')
    entries = [e for e in entries if e and e.split(b'\t', 1)[1] != b'.github/custom-release/request.json']
    return hashlib.sha256(b'\0'.join(entries)).hexdigest()


def patch_id(sha, cwd):
    patch = run('git', 'show', '--format=', '--no-ext-diff', sha, cwd=cwd)
    return run('git', 'patch-id', '--stable', cwd=cwd, data=patch).split()[0].decode()


def metadata(sha, cwd):
    return run('git', 'show', '-s', '--format=%an%x00%ae%x00%aI%x00%B', sha, cwd=cwd)


def validate_request(request):
    if request.get('schema') != 1:
        raise RuntimeError('Unsupported request schema')
    for key in ('expected_head', 'upstream_sha'):
        if not SHA.fullmatch(request.get(key, '')):
            raise RuntimeError('Invalid immutable SHA: ' + key)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request.get('request_id', '')):
        raise RuntimeError('Invalid request_id')
    if 'expected_tree' in request and not SHA.fullmatch(request['expected_tree']):
        raise RuntimeError('Invalid expected tree')


def remote_head(ref):
    rows = git('ls-remote', '--exit-code', '--heads', 'origin', 'refs/heads/' + ref).splitlines()
    if len(rows) != 1:
        raise RuntimeError('Missing or ambiguous remote branch')
    return rows[0].split()[0]


def main():
    if os.environ['GITHUB_REPOSITORY'] != REPO or os.environ['GITHUB_REF'] != 'refs/heads/' + CONTROL:
        raise RuntimeError('Wrong repository or control branch')
    request = json.loads(Path('.github/custom-sync/request.json').read_text())
    validate_request(request)
    expected = request['expected_head']
    upstream = request['upstream_sha']
    if remote_head(BRANCH) != expected:
        raise RuntimeError('User branch changed; refusing to overwrite it')
    current_upstream = git('ls-remote', '--exit-code', 'https://github.com/SagerNet/sing-box.git',
                           'refs/heads/testing').split()[0]
    if current_upstream != upstream:
        raise RuntimeError('Upstream moved; review and submit a fresh request')
    git('fetch', '--no-tags', 'origin', expected, CI_COMMIT, *ORIGINALS)
    git('fetch', '--no-tags', 'https://github.com/SagerNet/sing-box.git', upstream)
    state_path = Path('.github/custom-sync/state.json')
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    # Never discard unexpected user edits, even if the requester knows the new SHA.
    accepted_fingerprint = state['source_fingerprint'] if state else fingerprint(ORIGINAL_HEAD)
    if fingerprint(expected) != accepted_fingerprint:
        raise RuntimeError('Custom source changed outside this sync; review new commits before replay')
    if state and state['upstream_sha'] == upstream and state['ci_commit'] == CI_COMMIT:
        print('Upstream and custom source unchanged; no rewrite or release needed')
        Path('sync-result.json').write_text(json.dumps(dict(state, skip=True), indent=2) + '\n')
        return
    work = Path(os.environ['RUNNER_TEMP']) / 'custom-cherry-pick'
    git('worktree', 'add', '--detach', str(work), upstream)
    git('config', 'user.name', 'github-actions[bot]', cwd=work)
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com', cwd=work)
    mapping = []
    for original in ORIGINALS + [CI_COMMIT]:
        git('cherry-pick', original, cwd=work)
        replayed = git('rev-parse', 'HEAD', cwd=work)
        if metadata(original, work) != metadata(replayed, work):
            raise RuntimeError('Cherry-pick altered original author/date/message')
        before, after = patch_id(original, work), patch_id(replayed, work)
        if before != after:
            raise RuntimeError('Cherry-pick patch differs; manual review required')
        mapping.append({'original': original, 'cherry_pick': replayed,
                        'patch_id': after, 'metadata_preserved': True})
    new_head = git('rev-parse', 'HEAD', cwd=work)
    tree = git('rev-parse', 'HEAD^{tree}', cwd=work)
    if request.get('expected_tree') and request['expected_tree'] != tree:
        raise RuntimeError('Final tree differs from independently audited tree')
    if git('status', '--porcelain', cwd=work):
        raise RuntimeError('Cherry-pick worktree is dirty')
    result = {'schema': 1, 'skip': False, 'upstream_sha': upstream, 'ci_commit': CI_COMMIT,
              'previous_head': expected, 'source_sha': new_head, 'source_tree': tree,
              'source_fingerprint': fingerprint(new_head, work), 'mapping': mapping,
              'backup_branch': 'backup/custom-dev-before-sync-' + request['request_id']}
    print(json.dumps(result, indent=2))
    # Publish immutable audit artifact even if a later lease rejects the write.
    Path('sync-result.json').write_text(json.dumps(result, indent=2) + '\n')
    if remote_head(BRANCH) != expected:
        raise RuntimeError('Branch changed during audit; no ref update made')
    backup = result['backup_branch']
    if git('ls-remote', '--heads', 'origin', 'refs/heads/' + backup):
        raise RuntimeError('Backup branch already exists; never replace it')
    git('push', 'origin', expected + ':refs/heads/' + backup)
    # The lease is evaluated atomically by GitHub, unlike a read-then-force API call.
    git('push', '--force-with-lease=refs/heads/' + BRANCH + ':' + expected,
        'origin', new_head + ':refs/heads/' + BRANCH, cwd=work)
    if remote_head(BRANCH) != new_head:
        raise RuntimeError('Remote verification failed')
    state_path.write_text(json.dumps(result, indent=2) + '\n')
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('add', str(state_path))
    git('commit', '-m', 'Record verified cherry-pick sync result')
    git('push', 'origin', 'HEAD:refs/heads/' + CONTROL)


if __name__ == '__main__':
    main()
