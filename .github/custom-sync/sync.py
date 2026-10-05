#!/usr/bin/env python3
"""Audited real cherry-picks with one atomic source, backup and state transaction."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

REPO = 'csbxd/sing-box'
CONTROL = 'maintenance/custom-sync'
UPSTREAM = 'https://github.com/SagerNet/sing-box.git'
SHA = re.compile(r'[0-9a-f]{40}\Z')


def run(*args, cwd=None, data=None):
    return subprocess.check_output(args, cwd=cwd, input=data)


def git(*args, cwd=None):
    return run('git', *args, cwd=cwd).decode().strip()


def fingerprint(sha, cwd=None):
    entries = run('git', 'ls-tree', '-r', '-z', sha, cwd=cwd).split(b'\0')
    entries = [e for e in entries if e and e.split(b'\t', 1)[1] != b'.github/custom-release/request.json']
    return hashlib.sha256(b'\0'.join(entries)).hexdigest()


def patch_id(sha, cwd):
    patch = run('git', 'show', '--format=', '--binary', '--no-ext-diff', sha, cwd=cwd)
    result = run('git', 'patch-id', '--stable', cwd=cwd, data=patch).split()
    if not result:
        raise RuntimeError('Empty replay patch requires explicit review: ' + sha)
    return result[0].decode()


def metadata(sha, cwd):
    # Compare the raw author header and complete message, without stripping any bytes.
    raw = subprocess.check_output(['git', 'cat-file', 'commit', sha], cwd=cwd)
    headers, message = raw.split(b'\n\n', 1)
    authors = [line for line in headers.split(b'\n') if line.startswith(b'author ')]
    if len(authors) != 1:
        raise RuntimeError('Invalid author header')
    return authors[0] + b'\n\n' + message


def validate_request(request):
    if not isinstance(request, dict):
        raise RuntimeError('Request must be an object')
    if request.get('schema') == 1:
        targets = [dict(request, branch='custom-dev')]
    elif request.get('schema') == 2 and isinstance(request.get('targets'), list):
        targets = request['targets']
    else:
        raise RuntimeError('Unsupported request schema')
    if not targets or len(targets) > 100:
        raise RuntimeError('Expected 1..100 targets')
    if not isinstance(request.get('request_id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request['request_id']):
        raise RuntimeError('Invalid request_id')
    seen = set()
    for target in targets:
        if not isinstance(target, dict):
            raise RuntimeError('Target must be an object')
        branch = target.get('branch')
        if not isinstance(branch, str) or branch in seen:
            raise RuntimeError('Invalid or duplicate target branch')
        seen.add(branch)
        for key in ('expected_head', 'upstream_sha'):
            if not isinstance(target.get(key), str) or not SHA.fullmatch(target[key]):
                raise RuntimeError('Invalid immutable SHA: ' + key)
        if 'expected_tree' in target and (not isinstance(target['expected_tree'], str) or not SHA.fullmatch(target['expected_tree'])):
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


def ensure_request_current(targets, config, control_head):
    if remote_head(CONTROL) != control_head:
        raise RuntimeError('A newer control commit superseded this request')
    for target in targets:
        branch = target['branch']
        if remote_head(branch) != target['expected_head']:
            raise RuntimeError('User branch changed: ' + branch)
        upstream_ref = 'refs/heads/' + config[branch]['upstream_branch']
        rows = git('ls-remote', '--exit-code', '--heads', UPSTREAM, upstream_ref).splitlines()
        if len(rows) != 1 or rows[0].split() != [target['upstream_sha'], upstream_ref]:
            raise RuntimeError('Upstream moved; submit a fresh reviewed request: ' + branch)


def write_audit(result):
    Path('sync-result.json').write_text(json.dumps(result, indent=2) + '\n')



def patch_id_excluding(sha, cwd, paths):
    patch = run('git', 'show', '--format=', '--binary', '--no-ext-diff',
                '--no-textconv', sha, '--', '.',
                *[':(literal,exclude)' + path for path in paths], cwd=cwd)
    result = run('git', 'patch-id', '--stable', cwd=cwd, data=patch).split()
    return result[0].decode() if result else None


def replay_commit(original, work, branch, upstream, policy):
    resolutions = policy.get('resolutions', [])
    matches = [item for item in resolutions if item.get('original') == original]
    if len(matches) > 1:
        raise RuntimeError('Duplicate reviewed resolution for original commit')
    resolution = matches[0] if matches else None
    if resolution is not None:
        if resolution.get('branch') != branch or resolution.get('upstream_sha') != upstream:
            raise RuntimeError('Resolution does not match exact branch/upstream')
        files = resolution.get('files', [])
        if not isinstance(files, list) or not files:
            raise RuntimeError('Resolution has no reviewed conflict files')
        paths = [item['path'] for item in files]
        if len(set(paths)) != len(paths):
            raise RuntimeError('Resolution has duplicate paths')
        for item in files:
            path = Path(item['path'])
            fixture = Path(item['resolved_path'])
            if (path.is_absolute() or any(part in ('.', '..', '.git') for part in path.parts)
                    or not path.parts or item['path'] != path.as_posix()
                    or not item['resolved_path'].startswith('.github/custom-sync/compatibility/')
                    or fixture.is_absolute() or '..' in fixture.parts
                    or not SHA.fullmatch(item.get('blob_sha', ''))):
                raise RuntimeError('Unsafe or unpinned resolution path')
            if not fixture.is_file() or fixture.is_symlink():
                raise RuntimeError('Missing regular-file resolution fixture')
            if git('hash-object', str(fixture)) != item['blob_sha']:
                raise RuntimeError('Resolution fixture blob differs from reviewed policy')
    try:
        git('-c', 'rerere.enabled=false', 'cherry-pick', '--cleanup=verbatim', original, cwd=work)
    except subprocess.CalledProcessError as error:
        if error.output:
            print(error.output.decode(errors='replace'))
        conflicts = git('diff', '--name-only', '--diff-filter=U', cwd=work).splitlines()
        if resolution is None or set(conflicts) != set(paths):
            raise RuntimeError('Unreviewed cherry-pick conflicts in ' + branch + ': ' + ', '.join(conflicts)) from error
        for item in files:
            staged = git('ls-files', '--stage', '--', item['path'], cwd=work).splitlines()
            if not staged or any(row.split()[0] != '100644' for row in staged):
                raise RuntimeError('Resolution requires an ordinary reviewed text file')
            destination = work / item['path']
            if destination.is_symlink() or work.resolve() not in destination.resolve().parents:
                raise RuntimeError('Resolution would traverse outside replay worktree')
            destination.write_bytes(Path(item['resolved_path']).read_bytes())
            git('add', '--', item['path'], cwd=work)
        git('-c', 'core.editor=true', '-c', 'commit.cleanup=verbatim',
            'cherry-pick', '--continue', cwd=work)
    else:
        if resolution is not None:
            raise RuntimeError('Configured conflict resolution unexpectedly applied cleanly; review required')
    replayed = git('rev-parse', 'HEAD', cwd=work)
    if metadata(original, work) != metadata(replayed, work):
        raise RuntimeError('Cherry-pick altered original author/date/full message')
    before, after = patch_id(original, work), patch_id(replayed, work)
    mapping = {'original': original, 'cherry_pick': replayed,
               'patch_id': after, 'original_patch_id': before,
               'metadata_sha256': hashlib.sha256(metadata(replayed, work)).hexdigest(),
               'metadata_preserved': True}
    if resolution is None:
        if before != after:
            raise RuntimeError('Cherry-pick patch differs; manual review required')
        mapping['patch_equivalence'] = 'exact'
    else:
        original_unaffected = patch_id_excluding(original, work, paths)
        replayed_unaffected = patch_id_excluding(replayed, work, paths)
        if original_unaffected != replayed_unaffected:
            raise RuntimeError('Patch outside reviewed conflict files changed')
        for item in files:
            if git('rev-parse', replayed + ':' + item['path'], cwd=work) != item['blob_sha']:
                raise RuntimeError('Resolved commit differs from pinned reviewed file')
        mapping.update(patch_equivalence='reviewed-resolution',
                       unaffected_patch_id=original_unaffected,
                       resolution=resolution)
    return mapping


def main(prepare_only=False, request_path='.github/custom-sync/request.json'):
    if os.environ['GITHUB_REPOSITORY'] != REPO or os.environ['GITHUB_REF'] != 'refs/heads/' + CONTROL:
        raise RuntimeError('Wrong repository or control branch')
    control_head = os.environ['GITHUB_SHA']
    if not SHA.fullmatch(control_head) or git('rev-parse', 'HEAD') != control_head:
        raise RuntimeError('Checkout does not match the immutable workflow commit')
    if request_path != '.github/custom-sync/request.json' and (not prepare_only or request_path != '.github/custom-sync/validation-request.json'):
        raise RuntimeError('Alternate request path is allowed only for read-only validation')
    request = json.loads(Path(request_path).read_text())
    targets = validate_request(request)
    config = json.loads(Path('.github/custom-sync/branches.json').read_text())['branches']
    state_path = Path('.github/custom-sync/state.json')
    state = json.loads(state_path.read_text())
    if state.get('schema') != 2:
        raise RuntimeError('Branch state needs migration before any rewrite')
    for target in targets:
        branch = target['branch']
        if branch not in config or branch not in state['branches']:
            raise RuntimeError('Target has no reviewed replay policy: ' + branch)
        git('check-ref-format', 'refs/heads/' + branch)
        git('check-ref-format', 'refs/heads/' + config[branch]['upstream_branch'])
    ensure_request_current(targets, config, control_head)
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
        advertised = git('ls-remote', '--exit-code', UPSTREAM, upstream_ref).split()[0]
        if advertised != upstream:
            raise RuntimeError('Upstream moved: ' + branch)
        git('fetch', '--no-tags', 'origin', expected, recorded['source_sha'], *replay)
        verify_known_history(branch, expected, recorded)
        if recorded['upstream_sha'] == upstream and recorded['replay_commits'] == replay:
            if target.get('expected_tree') and target['expected_tree'] != git('rev-parse', expected + '^{tree}'):
                raise RuntimeError('No-op tree differs from independently audited tree')
            prepared.append({'branch': branch, 'skip': True, 'source_sha': expected})
            continue
        git('fetch', '--no-tags', UPSTREAM, upstream)
        work = Path(os.environ['RUNNER_TEMP']) / ('custom-cherry-pick-' + str(index))
        git('worktree', 'add', '--detach', str(work), upstream)
        git('config', 'user.name', 'github-actions[bot]', cwd=work)
        git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com', cwd=work)
        mapping = []
        for original in replay:
            mapping.append(replay_commit(original, work, branch, upstream, policy))
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
    result = {'schema': 2, 'request_id': request['request_id'],
              'control_commit': control_head, 'targets': prepared, 'applied': [],
              'status': 'prepared'}
    write_audit(result)
    print(json.dumps(result, indent=2))
    changed = [record for record in prepared if not record['skip']]
    ensure_request_current(targets, config, control_head)
    if prepare_only:
        result['status'] = 'validated-only'
        write_audit(result)
        print('Read-only preparation complete; no source, backup or state writes')
        return
    if not changed:
        result['status'] = 'noop'
        write_audit(result)
        print('All targets unchanged; no source, backup or state writes')
        return
    for record in changed:
        backup_ref = 'refs/heads/' + record['backup_branch']
        git('check-ref-format', backup_ref)
        if git('ls-remote', '--heads', 'origin', backup_ref):
            raise RuntimeError('Backup branch already exists; never replace it')
        state['branches'][record['branch']] = record
    # Prepare state locally. No remote ref has changed at this point.
    state_path.write_text(json.dumps(state, indent=2) + '\n')
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('add', str(state_path))
    git('commit', '-m', 'Record verified atomic branch replay sync results')
    state_commit = git('rev-parse', 'HEAD')
    if git('rev-parse', state_commit + '^') != control_head:
        raise RuntimeError('State commit is not based on the audited control head')
    result['state_commit'] = state_commit
    write_audit(result)
    ensure_request_current(targets, config, control_head)
    # One receive transaction covers every source, backup and state update.
    # Empty backup leases prohibit replacing even fast-forward-compatible refs.
    leases = ['--force-with-lease=refs/heads/' + CONTROL + ':' + control_head]
    refspecs = [state_commit + ':refs/heads/' + CONTROL]
    for record in changed:
        leases += ['--force-with-lease=refs/heads/' + record['branch'] + ':' + record['previous_head'],
                   '--force-with-lease=refs/heads/' + record['backup_branch'] + ':']
        refspecs += [record['source_sha'] + ':refs/heads/' + record['branch'],
                     record['previous_head'] + ':refs/heads/' + record['backup_branch']]
    try:
        git('push', '--atomic', *leases, 'origin', *refspecs)
    except Exception:
        # A transport failure can be ambiguous. Preserve the plan for inspection;
        # never automatically retry or try a non-atomic fallback.
        result['status'] = 'push_failed_inspect_remote'
        write_audit(result)
        raise
    result['status'] = 'pushed'
    result['applied'] = [record['branch'] for record in changed]
    write_audit(result)
    if remote_head(CONTROL) != state_commit:
        raise RuntimeError('Control changed after atomic push; inspect audit/state')
    for record in changed:
        if remote_head(record['branch']) != record['source_sha']:
            raise RuntimeError('Source changed after atomic push; inspect audit/state')
        if remote_head(record['backup_branch']) != record['previous_head']:
            raise RuntimeError('Backup verification failed; inspect audit/state')
    for record in prepared:
        if record['skip'] and remote_head(record['branch']) != record['source_sha']:
            raise RuntimeError('No-op source changed during atomic push; inspect audit/state')
    result['status'] = 'verified'
    write_audit(result)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--request-path', default='.github/custom-sync/request.json')
    args = parser.parse_args()
    main(prepare_only=args.prepare_only, request_path=args.request_path)
