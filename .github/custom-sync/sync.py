#!/usr/bin/env python3
"""Actual cherry-pick synchronization with metadata/patch audits and a lease."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

REPO = 'csbxd/sing-box'
CONTROL = 'maintenance/custom-sync'
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
    if request.get('schema') == 1:
        targets = [dict(request, branch='custom-dev')]
    elif request.get('schema') == 2 and isinstance(request.get('targets'), list):
        targets = request['targets']
    else:
        raise RuntimeError('Unsupported request schema')
    if not targets or len(targets) > 100:
        raise RuntimeError('Expected 1..100 targets')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request.get('request_id', '')):
        raise RuntimeError('Invalid request_id')
    seen = set()
    for target in targets:
        branch = target.get('branch')
        if not isinstance(branch, str) or branch in seen:
            raise RuntimeError('Invalid or duplicate target branch')
        seen.add(branch)
        for key in ('expected_head', 'upstream_sha'):
            if not SHA.fullmatch(target.get(key, '')):
                raise RuntimeError('Invalid immutable SHA: ' + key)
        if 'expected_tree' in target and not SHA.fullmatch(target['expected_tree']):
            raise RuntimeError('Invalid expected tree')
    return targets


def verify_known_history(branch, expected, state):
    recorded = state['source_sha']
    subprocess.run(['git', 'merge-base', '--is-ancestor', recorded, expected], check=True)
    if fingerprint(expected) != state['source_fingerprint']:
        raise RuntimeError('Custom source changed outside controlled replay: ' + branch)
    if recorded == expected:
        return
    if branch != 'custom-dev':
        raise RuntimeError('Unrecorded branch commits require review: ' + branch)
    for commit in git('rev-list', recorded + '..' + expected).splitlines():
        parents = git('rev-list', '--parents', '-n', '1', commit).split()
        if len(parents) != 2:
            raise RuntimeError('Unrecorded merge/root commit requires review')
        files = git('diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines()
        if files != ['.github/custom-release/request.json']:
            raise RuntimeError('Only release-request descendants may be ignored')


def remote_head(ref):
    rows = git('ls-remote', '--exit-code', '--heads', 'origin', 'refs/heads/' + ref).splitlines()
    if len(rows) != 1:
        raise RuntimeError('Missing or ambiguous remote branch')
    return rows[0].split()[0]


def main():
    if os.environ['GITHUB_REPOSITORY'] != REPO or os.environ['GITHUB_REF'] != 'refs/heads/' + CONTROL:
        raise RuntimeError('Wrong repository or control branch')
    request = json.loads(Path('.github/custom-sync/request.json').read_text())
    targets = validate_request(request)
    config = json.loads(Path('.github/custom-sync/branches.json').read_text())['branches']
    state_path = Path('.github/custom-sync/state.json')
    state = json.loads(state_path.read_text())
    if state.get('schema') != 2:
        raise RuntimeError('Branch state needs migration before any rewrite')
    prepared = []
    # Audit every requested target before any branch write. Unknown custom edits,
    # unreviewed branches and conflicts fail the entire preflight closed.
    for index, target in enumerate(targets):
        branch = target['branch']
        if branch not in config or branch not in state['branches']:
            raise RuntimeError('Target has no reviewed replay policy: ' + branch)
        policy = config[branch]
        recorded = state['branches'][branch]
        expected, upstream = target['expected_head'], target['upstream_sha']
        replay = policy['replay_commits']
        if any(not SHA.fullmatch(c) for c in replay):
            raise RuntimeError('Replay policy has a nonimmutable commit')
        if remote_head(branch) != expected:
            raise RuntimeError('User branch changed: ' + branch)
        upstream_ref = 'refs/heads/' + policy['upstream_branch']
        advertised = git('ls-remote', '--exit-code', 'https://github.com/SagerNet/sing-box.git', upstream_ref).split()[0]
        if advertised != upstream:
            raise RuntimeError('Upstream moved: ' + branch)
        git('fetch', '--no-tags', 'origin', expected, recorded['source_sha'], *replay)
        verify_known_history(branch, expected, recorded)
        if recorded['upstream_sha'] == upstream and recorded['replay_commits'] == replay:
            prepared.append({'branch': branch, 'skip': True, 'source_sha': expected})
            continue
        git('fetch', '--no-tags', 'https://github.com/SagerNet/sing-box.git', upstream)
        work = Path(os.environ['RUNNER_TEMP']) / ('custom-cherry-pick-' + str(index))
        git('worktree', 'add', '--detach', str(work), upstream)
        git('config', 'user.name', 'github-actions[bot]', cwd=work)
        git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com', cwd=work)
        mapping = []
        for original in replay:
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
        if target.get('expected_tree') and target['expected_tree'] != tree:
            raise RuntimeError('Final tree differs from independently audited tree')
        if git('status', '--porcelain', cwd=work):
            raise RuntimeError('Cherry-pick worktree is dirty')
        record = {'branch': branch, 'skip': False, 'upstream_sha': upstream,
                  'replay_commits': replay, 'previous_head': expected,
                  'source_sha': new_head, 'source_tree': tree,
                  'source_fingerprint': fingerprint(new_head, work), 'mapping': mapping,
                  'backup_branch': 'backup/sync-' + request['request_id'] + '/' + branch}
        prepared.append(record)
    result = {'schema': 2, 'targets': prepared, 'applied': []}
    Path('sync-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    try:
        for record in prepared:
            if record['skip']:
                print('Unchanged; no rewrite needed: ' + record['branch'])
                continue
            branch, expected = record['branch'], record['previous_head']
            if remote_head(branch) != expected:
                raise RuntimeError('Branch changed during audit; no overwrite: ' + branch)
            backup = record['backup_branch']
            if git('ls-remote', '--heads', 'origin', 'refs/heads/' + backup):
                raise RuntimeError('Backup branch already exists; never replace it')
            git('push', 'origin', expected + ':refs/heads/' + backup)
            git('push', '--force-with-lease=refs/heads/' + branch + ':' + expected,
                'origin', record['source_sha'] + ':refs/heads/' + branch)
            if remote_head(branch) != record['source_sha']:
                raise RuntimeError('Remote verification failed: ' + branch)
            state['branches'][branch] = record
            result['applied'].append(branch)
            Path('sync-result.json').write_text(json.dumps(result, indent=2) + '\n')
    finally:
        # Persist successful targets even if a later target's lease rejects a write.
        if result['applied']:
            state_path.write_text(json.dumps(state, indent=2) + '\n')
            git('config', 'user.name', 'github-actions[bot]')
            git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
            git('add', str(state_path))
            git('commit', '-m', 'Record verified branch replay sync results')
            git('push', 'origin', 'HEAD:refs/heads/' + CONTROL)


if __name__ == '__main__':
    main()
