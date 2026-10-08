#!/usr/bin/env python3
"""One-shot real push proof on two isolated, retained, inert probe branches."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import sync as sync_control

REQUEST_PATH = '.github/custom-sync/push-probe-request.json'
PROBE_PREFIX = 'permission-probe/'
WORKFLOW_PATH = '.github/workflows/permission-proof.yml'
AUDIT_PATH = Path('push-probe-result.json').resolve()
INERT_WORKFLOW = """name: Inert permission proof
on:
  workflow_dispatch:
permissions: {}
jobs:
  never:
    if: ${{ false }}
    runs-on: ubuntu-24.04
    steps:
      - run: ':'
"""


def git(*args, data=None, cwd=None):
    return subprocess.check_output(['git', *args], input=data, cwd=cwd).decode().strip()


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {'schema', 'request_id', 'code_commit'}:
        raise RuntimeError('Unexpected probe request fields')
    if type(request['schema']) is not int or request['schema'] != 1:
        raise RuntimeError('Unsupported probe schema')
    if not isinstance(request['request_id'], str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{5,59}', request['request_id']):
        raise RuntimeError('Invalid probe request ID')
    if not isinstance(request['code_commit'], str) or not re.fullmatch(r'[0-9a-f]{40}', request['code_commit']):
        raise RuntimeError('Expected exact reviewed code commit')
    return ['refs/heads/' + PROBE_PREFIX + request['request_id'] + '/' + suffix
            for suffix in ('content', 'workflow')]


def remote_snapshot():
    result = {}
    for line in git('ls-remote', '--refs', 'origin', 'refs/heads/*', 'refs/tags/*').splitlines():
        sha, ref = line.split()
        if (not re.fullmatch(r'[0-9a-f]{40}', sha)
                or not ref.startswith(('refs/heads/', 'refs/tags/')) or ref in result):
            raise RuntimeError('Invalid remote ref snapshot')
        result[ref] = sha
    if not result:
        raise RuntimeError('Empty remote ref snapshot')
    return result


def require_snapshot(expected):
    actual = remote_snapshot()
    if actual != expected:
        raise RuntimeError('Remote refs changed; stop and inspect without retry')
    return actual


def make_commit(request_id, phase, parent=None, with_workflow=False, cwd=None):
    def blob(content):
        return git('hash-object', '-w', '--stdin', data=content.encode(), cwd=cwd)
    def tree(entries):
        return git('mktree', data=('\n'.join(entries) + '\n').encode(), cwd=cwd)
    readme = blob('Isolated CUSTOM_SYNC_TOKEN permission proof.\nRequest: ' + request_id
                  + '\nPhase: ' + phase + '\nRetained for audit; no source code or release.\n')
    entries = ['100644 blob ' + readme + '\tPROBE.md']
    if with_workflow:
        workflows = tree(['100644 blob ' + blob(INERT_WORKFLOW + '# Unique request: ' + request_id + '\n') + '\tpermission-proof.yml'])
        github = tree(['040000 tree ' + workflows + '\tworkflows'])
        entries.insert(0, '040000 tree ' + github + '\t.github')
    root = tree(entries)
    args = ['-c', 'user.name=github-actions[bot]', '-c',
            'user.email=41898282+github-actions[bot]@users.noreply.github.com',
            'commit-tree', root]
    if parent:
        args += ['-p', parent]
    sha = git(*args, '-m', 'Record isolated push permission proof: ' + request_id + ' ' + phase, cwd=cwd)
    expected_paths = ['PROBE.md'] if not with_workflow else [WORKFLOW_PATH, 'PROBE.md']
    if git('ls-tree', '-r', '--name-only', sha, cwd=cwd).splitlines() != expected_paths:
        raise RuntimeError('Probe tree contains unexpected files')
    if git('rev-list', '--parents', '-n', '1', sha, cwd=cwd).split() != [sha] + ([parent] if parent else []):
        raise RuntimeError('Probe has unexpected ancestry')
    if with_workflow and subprocess.check_output(['git', 'show', sha + ':' + WORKFLOW_PATH], cwd=cwd).decode() != INERT_WORKFLOW + '# Unique request: ' + request_id + '\n':
        raise RuntimeError('Probe workflow differs from inert fixture')
    return sha


def push_args(refs, expected, heads):
    if len(refs) != 2 or len(set(refs)) != 2 or len(expected) != 2 or len(heads) != 2:
        raise RuntimeError('Expected exactly two isolated probe refs')
    for ref, old, new in zip(refs, expected, heads):
        if not re.fullmatch(r'refs/heads/permission-probe/[a-z0-9][a-z0-9-]{5,59}/(content|workflow)', ref):
            raise RuntimeError('Non-probe push destination')
        if old and not re.fullmatch(r'[0-9a-f]{40}', old):
            raise RuntimeError('Invalid exact old SHA')
        if not re.fullmatch(r'[0-9a-f]{40}', new):
            raise RuntimeError('Invalid exact new SHA')
    return ['--atomic', *['--force-with-lease=' + ref + ':' + old for ref, old in zip(refs, expected)],
            'origin', *[sha + ':' + ref for sha, ref in zip(heads, refs)]]


def write_audit(audit):
    AUDIT_PATH.write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')


def main():
    token = sync_control.take_push_token()
    if not token:
        raise RuntimeError('CUSTOM_SYNC_TOKEN is missing; no credential fallback')
    repo = os.environ.get('GITHUB_REPOSITORY')
    control = 'refs/heads/' + sync_control.CONTROL
    workflow_sha = os.environ.get('GITHUB_SHA', '')
    if (repo not in ('csbxd/sing-box', 'csbxd/sing-box-for-android') or repo != sync_control.REPO
            or os.environ.get('GITHUB_REF') != control or os.environ.get('GITHUB_EVENT_NAME') != 'push'
            or os.environ.get('GITHUB_RUN_ATTEMPT') != '1'):
        raise RuntimeError('Wrong repository/control/event or repeated attempt')
    if not re.fullmatch(r'[0-9a-f]{40}', workflow_sha) or git('rev-parse', 'HEAD') != workflow_sha:
        raise RuntimeError('Expected exact workflow HEAD')
    expected_url = 'https://github.com/' + repo
    for kind in ([], ['--push']):
        if git('remote', 'get-url', *kind, '--all', 'origin') not in (expected_url, expected_url + '.git'):
            raise RuntimeError('Unexpected origin URL')
    if git('diff', '--name-only', 'HEAD', '--', '.github/custom-sync', '.github/workflows'):
        raise RuntimeError('Uncommitted control changes')
    committed = subprocess.check_output(['git', 'show', workflow_sha + ':' + REQUEST_PATH])
    if committed != Path(REQUEST_PATH).read_bytes():
        raise RuntimeError('Request differs from exact workflow commit')
    request = json.loads(committed)
    refs = validate_request(request)
    if git('rev-list', '--parents', '-n', '1', workflow_sha).split() != [workflow_sha, request['code_commit']]:
        raise RuntimeError('Request must be sole child of exact reviewed code commit')
    if git('diff-tree', '--no-commit-id', '--name-only', '-r', workflow_sha).splitlines() != [REQUEST_PATH]:
        raise RuntimeError('Request commit must modify only the dedicated probe request')
    original_cwd = Path.cwd()
    # Fresh repository: no checkout-local config, credential headers or hooks.
    with tempfile.TemporaryDirectory(prefix='isolated-push-proof-') as directory:
        git('init', '-q', directory)
        try:
            os.chdir(directory)
            git('remote', 'add', 'origin', expected_url + '.git')
            baseline = remote_snapshot()
            if baseline.get(control) != workflow_sha or any(ref in baseline for ref in refs):
                raise RuntimeError('Stale control or probe already exists; no overwrite or retry')
            initial = make_commit(request['request_id'], 'contents-create')
            content = make_commit(request['request_id'], 'contents-update', parent=initial)
            workflow = make_commit(request['request_id'], 'workflow-update', parent=initial, with_workflow=True)
            audit = {'schema': 1, 'repository': repo, 'request_id': request['request_id'],
                     'control_commit': workflow_sha, 'reviewed_code_commit': request['code_commit'],
                     'baseline_refs': baseline, 'probe_refs': refs, 'initial_commit': initial,
                     'content_commit': content, 'workflow_commit': workflow, 'status': 'prepared',
                     'cleanup': 'retained-inert-probe-branches', 'contents_push_verified': False,
                     'workflow_push_verified': False, 'existing_refs_unchanged': False}
            write_audit(audit)
            try:
                require_snapshot(baseline)
                audit['status'] = 'contents-push-started'
                write_audit(audit)
                sync_control.atomic_push(token, *push_args(refs, ['', ''], [initial, initial]))
                after_create = dict(baseline, **dict(zip(refs, [initial, initial])))
                require_snapshot(after_create)
                audit.update(status='contents-push-verified', contents_push_verified=True)
                write_audit(audit)
                require_snapshot(after_create)
                audit['status'] = 'workflow-push-started'
                write_audit(audit)
                sync_control.atomic_push(token, *push_args(refs, [initial, initial], [content, workflow]))
                after_update = dict(baseline, **dict(zip(refs, [content, workflow])))
                require_snapshot(after_update)
                audit.update(status='verified-real-push', workflow_push_verified=True,
                             existing_refs_unchanged=True, final_refs=after_update)
                write_audit(audit)
                print(json.dumps(audit, indent=2, sort_keys=True))
            except Exception:
                audit['failed_stage'] = audit['status']
                audit['status'] = 'failed-inspect-remote-before-any-retry'
                try:
                    audit['observed_refs'] = remote_snapshot()
                except Exception:
                    audit['remote_inspection'] = 'unavailable'
                write_audit(audit)
                raise
        finally:
            os.chdir(original_cwd)


if __name__ == '__main__':
    main()
