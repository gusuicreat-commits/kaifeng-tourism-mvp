"""Export one committed, clean source revision with a file manifest; never export .env/DBs."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def forbidden(name):
    p = PurePosixPath(name)
    return (any(part in {'.git', '.venv', 'node_modules', '.backups', '.runtime',
                         '.test-results', '__pycache__', 'diagnostics', 'delivery'} for part in p.parts)
            or (p.name.startswith('.env') and p.name != '.env.example')
            or p.suffix.lower() in {'.db', '.sqlite3', '.log', '.pem', '.key'}
            or '.sqlite3-' in p.name or p.name.endswith(('.db-wal', '.db-shm')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if git('status', '--porcelain').strip():
        raise SystemExit('Uncommitted source changes exist. Review and commit before packaging.')
    revision = git('rev-parse', 'HEAD').decode().strip()
    raw = git('archive', '--format=zip', revision)
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        bad = [i.filename for i in source.infolist() if not i.is_dir() and forbidden(i.filename)]
        if bad:
            raise SystemExit('Refusing private/runtime files: ' + ', '.join(bad))
        manifest = {'git_commit': revision, 'scope': 'internal_team_source_handoff',
                    'includes_secrets_or_runtime_databases': False,
                    'files': [{'path': i.filename, 'size': i.file_size,
                               'sha256': hashlib.sha256(source.read(i)).hexdigest()}
                              for i in source.infolist() if not i.is_dir()]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents replacing an earlier delivered version.
        with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as output:
            prefix = 'kaifeng-tourism-mvp/'
            for info in source.infolist():
                data = source.read(info)
                info.filename = prefix + info.filename
                output.writestr(info, data)
            output.writestr(prefix + 'HANDOFF_VERSION.json',
                            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(args.output.suffix + '.sha256').write_text(
        digest + '  ' + args.output.name + '\n', encoding='utf-8')
    print(json.dumps({'archive': str(args.output.resolve()), 'git_commit': revision,
                      'files': len(manifest['files']), 'sha256': digest}, ensure_ascii=False))


if __name__ == '__main__':
    main()
