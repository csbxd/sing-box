#!/usr/bin/env python3
"""Plan a custom release without mutating GitHub or local source."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

SEMVER = re.compile(r"v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\Z")
MARKER = re.compile(r"<!-- custom-core-release: (\{.*?\}) -->")


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def releases(repo):
    pages = json.loads(run('gh', 'api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'))
    return [item for page in pages for item in page]


def custom_tag(base, revision):
    version = base.removeprefix('v')
    if not SEMVER.fullmatch(version):
        raise ValueError('Unsupported upstream release version: ' + base)
    return 'v' + version + ('.c' if '-' in version else '-c') + str(revision)


def next_revision(base, tags):
    prefix = custom_tag(base, 1)[:-1]
    numbers = [int(tag[len(prefix):]) for tag in tags
               if tag.startswith(prefix) and tag[len(prefix):].isdigit()]
    return max(numbers, default=0) + 1


def metadata(release):
    match = MARKER.search(release.get('body') or '')
    return json.loads(match[1]) if match else None


def nearest_release(items, target):
    reachable = []
    for item in items:
        if item['draft'] or not SEMVER.fullmatch(item['tag_name']):
            continue
        ref = 'refs/upstream-release-tags/' + item['tag_name']
        # Missing advertised upstream tags are fatal: never guess a newer version.
        commit = run('git', 'rev-parse', ref + '^{commit}')
        result = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, target], check=False)
        if result.returncode == 1:
            continue
        if result.returncode:
            raise RuntimeError('Cannot establish upstream ancestry')
        distance = int(run('git', 'rev-list', '--count', f'{commit}..{target}'))
        reachable.append((distance, item['tag_name'], item))
    if not reachable:
        raise RuntimeError('No published upstream Release is reachable from source')
    # Git graph distance, then tag name for deterministic ties. Includes prereleases.
    return min(reachable, key=lambda x: (x[0], x[1]))[2]


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    if repo != 'csbxd/sing-box':
        raise RuntimeError('This workflow is restricted to csbxd/sing-box')
    sha = run('git', 'rev-parse', 'HEAD')
    if os.environ.get('GITHUB_EVENT_NAME') == 'push':
        request = json.loads(Path('.github/custom-release/request.json').read_text())
        if request.get('schema') != 1 or not re.fullmatch(r'[0-9a-f]{40}', request.get('source_sha', '')):
            raise RuntimeError('Invalid immutable release request')
        sha = request['source_sha']
        subprocess.run(['git', 'merge-base', '--is-ancestor', sha, 'HEAD'], check=True)
    tree = run('git', 'rev-parse', sha + '^{tree}')
    entries = subprocess.check_output(['git', 'ls-tree', '-r', '-z', sha]).split(b'\0')
    entries = [e for e in entries if e and e.split(b'\t', 1)[1] != b'.github/custom-release/request.json']
    fingerprint = hashlib.sha256(b'\0'.join(entries)).hexdigest()
    existing = releases(repo)
    for release in existing:
        info = metadata(release)
        if info and info.get('source_fingerprint', info.get('source_tree')) in (fingerprint, tree):
            if release['draft']:
                raise RuntimeError('A draft for this source already exists; inspect it before retrying')
            Path('release-plan.json').write_text(json.dumps({'skip': True, 'existing': release['html_url']}) + '\n')
            with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
                out.write('skip=true\n')
            print('Unchanged source; already released: ' + release['html_url'])
            return
    run('git', 'fetch', '--no-tags', 'https://github.com/SagerNet/sing-box.git',
        '+refs/tags/*:refs/upstream-release-tags/*')
    base = nearest_release(releases('SagerNet/sing-box'), sha)
    tags = run('git', 'tag', '--list').splitlines()
    tags += [r['tag_name'] for r in existing]
    tag = custom_tag(base['tag_name'], next_revision(base['tag_name'], tags))
    plan = {'skip': False, 'tag': tag, 'version': tag[1:], 'source_sha': sha,
            'source_tree': tree, 'source_fingerprint': fingerprint, 'upstream_release': base['tag_name'],
            'prerelease': bool(base['prerelease']), 'upstream_url': base['html_url']}
    Path('release-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        for key in ('tag', 'version', 'source_sha'):
            out.write(f'{key}={plan[key]}\n')
        out.write('skip=false\n')
    print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    main()
