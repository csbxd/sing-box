#!/usr/bin/env python3
"""Read-only validation for exact prepared subset candidates; never pushes refs."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

SHA = re.compile(r'[0-9a-f]{40}\Z')


def select(audit, request, workflow_sha):
    if audit.get('schema') != 2 or audit.get('status') != 'validated-only':
        raise ValueError('Candidate audit is not successfully validated-only')
    if audit.get('control_commit') != workflow_sha or audit.get('request_id') != request.get('request_id'):
        raise ValueError('Candidate audit does not match immutable request')
    rows = audit.get('targets')
    targets = request.get('targets')
    if not isinstance(rows, list) or not rows or not isinstance(targets, list):
        raise ValueError('Missing candidate targets')
    names = [row.get('branch') for row in rows]
    if len(set(names)) != len(names) or names != [row.get('branch') for row in targets]:
        raise ValueError('Candidate target set/order does not match request')
    for row in rows:
        if type(row.get('skip')) is not bool or not SHA.fullmatch(row.get('source_sha', '')):
            raise ValueError('Malformed candidate source')
        if row['skip'] and row['source_sha'] != targets[names.index(row['branch'])].get('expected_head'):
            raise ValueError('No-op source does not match request')
        if not row['skip']:
            if not SHA.fullmatch(row.get('source_tree', '')):
                raise ValueError('Missing exact candidate tree')
            if row.get('upstream_sha') != targets[names.index(row['branch'])].get('upstream_sha'):
                raise ValueError('Candidate upstream does not match request')
            mapping = row.get('mapping')
            replay = row.get('replay_commits')
            if not isinstance(mapping, list) or not isinstance(replay, list):
                raise ValueError('Missing replay metadata')
            if [item.get('original') for item in mapping] != replay:
                raise ValueError('Replay mapping does not match ordered policy')
            if any(item.get('metadata_preserved') is not True for item in mapping):
                raise ValueError('Replay metadata check failed')
    custom = next((row['source_sha'] for row in rows if row['branch'] == 'custom-dev'), '')
    subset = [row for row in rows if row['branch'] != 'custom-dev' and not row['skip']]
    return custom, subset


def validate_subset(rows):
    for index, row in enumerate(rows):
        with tempfile.TemporaryDirectory(prefix='candidate-subset-', dir=os.environ['RUNNER_TEMP']) as directory:
            work = Path(directory) / 'source'
            subprocess.run(['git', 'worktree', 'add', '--detach', str(work), row['source_sha']], check=True)
            try:
                actual_tree = subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=work).decode().strip()
                if actual_tree != row['source_tree']:
                    raise ValueError('Prepared source tree changed')
                if not (work / 'go.mod').is_file():
                    # The reviewed docs branch contains generated static documentation.
                    if row['branch'] != 'docs' or row['source_sha'] != row['upstream_sha']:
                        raise ValueError('Non-Go candidate requires an explicit validation policy')
                    json.loads((work / 'schema.json').read_text())
                    if not (work / 'index.html').is_file():
                        raise ValueError('Missing generated documentation index')
                    print('Validated exact generated docs tree: ' + actual_tree, flush=True)
                    continue
                tags = (work / 'release/DEFAULT_BUILD_TAGS_OTHERS').read_text().strip()
                ldflags = (work / 'release/LDFLAGS').read_text().strip()
                print('Testing exact candidate ' + row['branch'] + ' ' + row['source_sha'], flush=True)
                subprocess.run(['go', 'test', '-v', '-exec', 'sudo', '-tags', tags,
                                '-ldflags', ldflags, './...'], cwd=work, check=True)
            finally:
                subprocess.run(['git', 'worktree', 'remove', '--force', str(work)], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-subset', action='store_true')
    args = parser.parse_args()
    audit = json.loads(Path('sync-result.json').read_text())
    request = json.loads(Path('.github/custom-sync/validation-request.json').read_text())
    custom, subset = select(audit, request, os.environ['GITHUB_SHA'])
    if args.run_subset:
        validate_subset(subset)
    else:
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('custom_source=' + custom + '\n')
        print(json.dumps({'custom_source': custom, 'subset': subset}, indent=2))


if __name__ == '__main__':
    main()
