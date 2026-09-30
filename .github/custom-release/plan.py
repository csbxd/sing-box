#!/usr/bin/env python3
"""Plan a custom release without mutating GitHub or local source."""
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

SEMVER = re.compile(r"v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\Z")
MARKER = re.compile(r"<!-- custom-core-release: (\{.*?\}) -->")


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def releases(repo):
    # Upstream releases have hundreds of assets: large pages can exceed GitHub's
    # response limits. Retry only the failed small page, never discard pagination.
    result = []
    page = 1
    while True:
        for attempt in range(4):
            try:
                batch = json.loads(run('gh', 'api', f'repos/{repo}/releases?per_page=10&page={page}'))
                if not isinstance(batch, list):
                    raise RuntimeError('Invalid release list response')
                break
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))
        result.extend(batch)
        if len(batch) < 10:
            return result
        page += 1


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


def source_version(changelog):
    for line in changelog.splitlines():
        match = re.fullmatch(r"#{1,6}\s+v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\s*", line)
        if match:
            return match[1]
    raise RuntimeError('No version heading in source changelog; refuse to guess')


def version_key(tag):
    version = tag.removeprefix('v')
    main, separator, pre = version.partition('-')
    # SemVer: stable > prerelease, numeric identifiers ordered numerically and
    # below text identifiers; a longer equal-prefix prerelease sorts later.
    identifiers = tuple((0, int(x)) if x.isdigit() else (1, x) for x in pre.split('.'))
    return tuple(map(int, main.split('.'))), not bool(separator), identifiers


def select_series_release(items, version):
    series = version.split('-', 1)[0]
    candidates = [item for item in items if not item['draft']
                  and SEMVER.fullmatch(item['tag_name'])
                  and item['tag_name'].removeprefix('v').split('-', 1)[0] == series]
    if not candidates:
        raise RuntimeError('No published upstream Release matches source series ' + series)
    return max(candidates, key=lambda item: version_key(item['tag_name']))


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
    version = source_version(run('git', 'show', sha + ':docs/changelog.md'))
    base = select_series_release(releases('SagerNet/sing-box'), version)
    tags = run('git', 'tag', '--list').splitlines()
    tags += [r['tag_name'] for r in existing]
    tag = custom_tag(base['tag_name'], next_revision(base['tag_name'], tags))
    plan = {'skip': False, 'request_commit': os.environ['GITHUB_SHA'], 'tag': tag, 'version': tag[1:], 'source_sha': sha,
            'source_tree': tree, 'source_fingerprint': fingerprint, 'upstream_release': base['tag_name'],
            'prerelease': bool(base['prerelease']), 'upstream_url': base['html_url'],
            'source_version': version, 'version_policy': 'source-series-latest'}
    Path('release-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        for key in ('tag', 'version', 'source_sha'):
            out.write(f'{key}={plan[key]}\n')
        out.write('skip=false\n')
    print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    main()
