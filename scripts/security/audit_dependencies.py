"""Check exact requirement pins, installed packages and locked crates against OSV."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import re
import sys
import tomllib
import urllib.request


def packages(root, installed=False):
    found = set()
    if installed:
        for distribution in importlib.metadata.distributions():
            name = re.sub(r'[-_.]+', '-', distribution.metadata['Name']).lower()
            found.add(('PyPI', name, distribution.version))
    for path in (root / 'requirements').glob('*.txt'):
        for line in path.read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s#]+)', line.strip())
            if match:
                name = re.sub(r'[-_.]+', '-', match[1]).lower()
                found.add(('PyPI', name, match[2]))
    lock = tomllib.loads((root / 'native/Cargo.lock').read_text(encoding='utf-8'))
    for entry in lock['package']:
        if entry.get('source', '').startswith('registry+'):
            found.add(('crates.io', entry['name'], entry['version']))
    return [{'package': {'ecosystem': ecosystem, 'name': name}, 'version': version}
            for ecosystem, name, version in sorted(found)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed', action='store_true', help='Also audit this Python environment, including transitive dependencies')
    args = parser.parse_args()
    queries = packages(Path(__file__).resolve().parents[2], args.installed)
    request = urllib.request.Request('https://api.osv.dev/v1/querybatch',
                                     data=json.dumps({'queries': queries}).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=45) as response:
        results = json.load(response)['results']
    if len(results) != len(queries):
        raise ValueError('Incomplete vulnerability response')
    affected = []
    for package, result in zip(queries, results):
        if result.get('vulns'):
            affected.append(dict(package, advisories=[item['id'] for item in result['vulns']]))
    print(json.dumps({'packages_checked': len(queries), 'affected': affected,
                      'scope': 'Requirement pins and Cargo.lock, plus installed Python environment' if args.installed
                               else 'Requirement pins and Cargo.lock; transitive Python dependencies require --installed'}, indent=2))
    return 1 if affected else 0


if __name__ == '__main__':
    sys.exit(main())
