#!/usr/bin/env python3
"""Mirror VNDB's four documented public dumps into complete, immutable releases."""
import base64
import datetime as dt
import email.utils
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

SOURCES = (
    'vndb-db-latest.tar.zst',
    'vndb-tags-latest.json.gz',
    'vndb-traits-latest.json.gz',
    'vndb-votes-latest.gz',
)
BASE = 'https://dl.vndb.org/dump/'
PART_SIZE = 1900 * 1024 * 1024


def run(*args, capture=False):
    return subprocess.run(args, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fingerprint(records):
    # Retrieval time / HTTP metadata must not create duplicate releases.
    content = '\n'.join(f"{r['name']}:{r['sha256']}" for r in sorted(records, key=lambda r: r['name']))
    return hashlib.sha256(content.encode()).hexdigest()


def download(name, directory):
    path = directory / name
    headers = directory / (name + '.headers')
    run('curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
        '--retry', '4', '--retry-all-errors', '--retry-delay', '15',
        '--connect-timeout', '30', '--max-time', '1800', '--silent', '--show-error',
        '--dump-header', str(headers), '--output', str(path), BASE + name)
    if not path.stat().st_size:
        raise ValueError(f'Empty download: {name}')
    # curl may record proxy/redirect/retry headers; only use the final response.
    final_headers = headers.read_text().strip().replace('\r\n', '\n').split('\n\n')[-1]
    metadata = {}
    for line in final_headers.splitlines()[1:]:
        key, sep, value = line.partition(':')
        if sep:
            metadata[key.lower()] = value.strip()
    if 'content-length' in metadata and int(metadata['content-length']) != path.stat().st_size:
        raise ValueError(f'Truncated download: {name}')
    modified = metadata.get('last-modified')
    if modified:
        modified = email.utils.parsedate_to_datetime(modified).isoformat()
    return {'name': name, 'url': BASE + name, 'size': path.stat().st_size,
            'sha256': sha256(path), 'last_modified': modified,
            'etag': metadata.get('etag')}


def validate(path):
    if path.name.endswith('.tar.zst'):
        run('zstd', '--test', '--quiet', str(path))
        # Read the tar structure without extracting untrusted paths.
        with open(os.devnull, 'w') as sink:
            subprocess.run(['tar', '--use-compress-program=zstd', '-tf', str(path)],
                           stdout=sink, check=True)
    elif path.name.endswith('.json.gz'):
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            data = json.load(stream)
        if not isinstance(data, list) or not data:
            raise ValueError(f'Expected a nonempty JSON array: {path.name}')
    else:
        # Reading the complete stream verifies gzip CRC and detects truncation.
        with gzip.open(path, 'rb') as stream:
            while stream.read(1024 * 1024):
                pass


def split_asset(path, limit=PART_SIZE):
    if path.stat().st_size <= limit:
        return [path]
    parts = []
    with path.open('rb') as source:
        index = 1
        while source.tell() < path.stat().st_size:
            part = path.with_name(f'{path.name}.part{index:03d}')
            remaining = limit
            with part.open('wb') as target:
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        break
                    target.write(chunk)
                    remaining -= len(chunk)
            parts.append(part)
            index += 1
    return parts


def get_release(tag):
    result = subprocess.run(['gh', 'api', f'repos/{os.environ["GH_REPO"]}/releases/tags/{tag}'],
                            capture_output=True, text=True)
    if result.returncode:
        if '(HTTP 404)' in result.stderr:
            # The tag endpoint excludes unpublished drafts, even for their
            # creator. The authenticated list endpoint includes drafts.
            pages = json.loads(run('gh', 'api', '--paginate', '--slurp',
                                   f'repos/{os.environ["GH_REPO"]}/releases?per_page=100',
                                   capture=True))
            return next((release for page in pages for release in page
                         if release['tag_name'] == tag), None)
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def verify_assets(release, paths):
    actual = {a['name']: a for a in release['assets']}
    if set(actual) != {p.name for p in paths}:
        raise ValueError('Release asset set does not match the complete manifest')
    for path in paths:
        asset = actual[path.name]
        if asset['state'] != 'uploaded' or asset['size'] != path.stat().st_size:
            raise ValueError(f'Incomplete uploaded asset: {path.name}')
        # Modern GitHub API supplies server-computed SHA-256 digests.
        if asset.get('digest') and asset['digest'] != 'sha256:' + sha256(path):
            raise ValueError(f'Uploaded checksum mismatch: {path.name}')


def publish(tag, directory, paths, notes):
    release = get_release(tag)
    if release and not release['draft']:
        verify_assets(release, paths)
        return 'Already synchronized: ' + release['html_url']
    notes_path = directory / 'release-notes.md'
    notes_path.write_text(notes, encoding='utf-8')
    if not release:
        run('gh', 'release', 'create', tag, '--draft', '--target', os.environ['GITHUB_SHA'],
            '--title', 'VNDB dump ' + json.loads(directory.joinpath('manifest.json').read_text(encoding='utf-8'))['snapshot_date'],
            '--notes-file', str(notes_path))
    # Only repair drafts; published historical releases are never overwritten.
    # gh uploads files as streams, including multi-GB assets.
    for path in paths:
        run('gh', 'release', 'upload', tag, str(path), '--clobber')
    release = get_release(tag)
    verify_assets(release, paths)
    run('gh', 'release', 'edit', tag, '--notes-file', str(notes_path),
        '--draft=false', '--latest')
    return 'Published: ' + get_release(tag)['html_url']


def keep_schedule_active():
    # Public scheduled workflows can be disabled after 60 days of inactivity.
    # One tiny monthly commit keeps this unattended mirror active. This path
    # is excluded from the workflow's push filters and never triggers a loop.
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        return
    month = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m') + '\n'
    endpoint = f'repos/{os.environ["GH_REPO"]}/contents/.github/sync-heartbeat'
    response = subprocess.run(['gh', 'api', endpoint + '?ref=main'], capture_output=True, text=True)
    current = None
    if response.returncode == 0:
        current = json.loads(response.stdout)
        if base64.b64decode(current['content']).decode() == month:
            return
    elif '(HTTP 404)' not in response.stderr:
        raise RuntimeError(response.stderr)
    payload = {'message': 'chore: monthly synchronization heartbeat', 'branch': 'main',
               'content': base64.b64encode(month.encode()).decode()}
    if current:
        payload['sha'] = current['sha']
    subprocess.run(['gh', 'api', '--method', 'PUT', endpoint, '--input', '-', '--silent'],
                   input=json.dumps(payload), text=True, check=True)


def main():
    for key in ('GH_TOKEN', 'GH_REPO', 'GITHUB_SHA'):
        if not os.environ.get(key):
            raise RuntimeError(f'Missing {key}')
    with tempfile.TemporaryDirectory(prefix='vndb-dump-') as temp:
        directory = Path(temp)
        records, paths = [], []
        for name in SOURCES:
            print(f'Downloading and verifying {name}', flush=True)
            records.append(download(name, directory))
            validate(directory / name)
            paths.extend(split_asset(directory / name))
        digest = fingerprint(records)
        snapshot_date = (records[0]['last_modified'] or dt.datetime.now(dt.timezone.utc).isoformat())[:10]
        manifest = {
            'schema_version': 1, 'snapshot_date': snapshot_date,
            'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'source_documentation': 'https://vndb.org/d14',
            'data_license': 'https://vndb.org/d17#4',
            'bundle_sha256': digest, 'files': records,
            'assets': [{'name': p.name, 'size': p.stat().st_size, 'sha256': sha256(p)} for p in paths],
        }
        manifest_path = directory / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        paths.append(manifest_path)
        checksums = directory / 'SHA256SUMS'
        checksums.write_text(''.join(f'{sha256(p)}  {p.name}\n' for p in paths), encoding='utf-8')
        paths.append(checksums)
        tag = 'dump-' + digest[:24]
        existing = get_release(tag)
        if existing and not existing['draft']:
            # Volatile manifest retrieval time differs on reruns. Verify the
            # deterministic dump assets, while requiring both metadata files.
            dump_release = dict(existing, assets=[a for a in existing['assets']
                                                 if a['name'] not in ('manifest.json', 'SHA256SUMS')])
            verify_assets(dump_release, paths[:-2])
            if not {'manifest.json', 'SHA256SUMS'} <= {a['name'] for a in existing['assets']}:
                raise ValueError('Published release is missing metadata')
            result = 'Already synchronized: ' + existing['html_url']
        else:
            notes = (f'VNDB public database dumps · {snapshot_date}\n\n'
                     'Source: https://vndb.org/d14\n\n'
                     'Original files are mirrored unchanged. Images are not included. '
                     'Files larger than 1900 MiB are split into numbered parts.\n\n'
                     'Verify downloads with `sha256sum -c SHA256SUMS`. '
                     'Source timestamps, sizes and hashes are recorded in `manifest.json`.\n\n'
                     'Data licensing: https://vndb.org/d17#4 (ODbL / DbCL and table-specific exceptions). '
                     'The original database archive includes its own README and license information.\n\n'
                     f'Bundle SHA-256: `{digest}`\n')
            result = publish(tag, directory, paths, notes)
        print(result)
        keep_schedule_active()
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
                summary.write(result + '\n')


if __name__ == '__main__':
    main()
