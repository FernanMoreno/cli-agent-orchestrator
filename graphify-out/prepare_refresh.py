"""Prepare bounded extraction prompts for this graph refresh (derived data only)."""
import json
from pathlib import Path

out = Path('graphify-out')
detect = json.loads((out / '.graphify_detect.json').read_text())
uncached = set((out / '.graphify_uncached.txt').read_text().splitlines())
docs = sorted(p for p in detect['files']['document'] if p in uncached)
images = sorted(p for p in detect['files']['image'] if p in uncached)
chunks = [docs[i:i + 22] for i in range(0, len(docs), 22)] + [[p] for p in images]
template = Path('/home/felni/.codex/skills/graphify/references/extraction-spec.md').read_text().split('```')[1].strip()
directory = out / 'refresh-chunks'
directory.mkdir(exist_ok=True)
manifest = []
for i, files in enumerate(chunks):
    prompt = template.replace('FILE_LIST', '\n'.join(files)).replace('CHUNK_NUM', str(i + 1)).replace('TOTAL_CHUNKS', str(len(chunks))).replace('DEEP_MODE (if --mode deep)', 'DEEP_MODE=false (not requested)')
    path = directory / f'prompt-{i:02}.txt'
    path.write_text(prompt)
    manifest.append({'chunk': i, 'files': files, 'prompt': str(path), 'output': str(directory / f'chunk-{i:02}.json')})
(directory / 'manifest.json').write_text(json.dumps(manifest, indent=2))
print(f'{len(docs)} documents + {len(images)} images = {len(chunks)} chunks')
print('Document chunks: 0..18; image chunks: 19..49')
