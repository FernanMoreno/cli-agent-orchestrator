"""Run identical Graphify AST extraction on a hash-verified Linux snapshot."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from graphify.extract import extract

root = Path.cwd()
out = root / 'graphify-out'
detected = json.loads((out / '.graphify_detect.json').read_text())
stage = Path(tempfile.mkdtemp(prefix='caos-graphify-', dir='/tmp'))
paths = []
hashes = {}
for source in detected['files']['code']:
    source = Path(source)
    relative = source.relative_to(root)
    target = stage / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    hashes[str(relative)] = digest
    paths.append(target)
print('SNAPSHOT_READY', stage, len(paths), flush=True)
result = extract(paths, cache_root=stage, root=stage)
for relative, digest in hashes.items():
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, ('source drift', relative)
for key in ('nodes', 'edges'):
    for item in result.get(key, []):
        source = item.get('source_file')
        if source and Path(source).is_absolute():
            item['source_file'] = str(Path(source).relative_to(stage))
        assert not str(item.get('source_file', '')).startswith('/tmp/')
result['failed_sources'] = [str(root / Path(p).relative_to(stage)) if Path(p).is_absolute() else p for p in result.get('failed_sources', [])]
(out / '.graphify_ast_staged.json').write_text(json.dumps(result))
(out / 'refresh-snapshot.json').write_text(json.dumps({'temporary_root': str(stage), 'files': hashes, 'verified_before_and_after': True}, indent=2))
print('AST_STAGED_VERIFIED', len(result['nodes']), len(result['edges']), 'failed', len(result.get('failed_sources', [])), flush=True)
