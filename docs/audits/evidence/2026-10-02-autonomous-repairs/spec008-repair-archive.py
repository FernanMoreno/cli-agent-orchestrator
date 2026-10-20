"""Archive reviewable evidence without native credential stores or live DBs."""
import hashlib,json,re
from pathlib import Path

archive=Path('docs/audits/evidence/2026-10-02-autonomous-repairs')
archive.mkdir(parents=True,exist_ok=True)
manifest_path=archive/'evidence-index.json'
manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'files':{}}

def save(source,destination):
    raw=source.read_bytes()
    text=raw.decode('utf-8',errors='replace')
    text=re.sub(r'CAO[-_]TURN[-_]RECEIPT[-_][0-9a-f]{32}','[receipt-redacted]',text)
    text=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[credential-redacted]',text)
    text=re.sub(r'Bearer\s+[A-Za-z0-9._~-]+','Bearer [credential-redacted]',text,flags=re.I)
    target=archive/destination;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
    manifest['files'][str(destination)]={'source':str(source),'original_sha256':hashlib.sha256(raw).hexdigest(),'archived_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'receipt_and_credential_redaction':True}

for p in Path('/tmp').glob('spec008-repair-*'):
    if p.is_file() and p.suffix in {'.log','.json','.py','.txt','.cjs','.patch'}:
        save(p,Path(p.name))
for root in Path('/var/tmp').glob('cao-spec008-repair-*'):
    if not root.is_dir() or 'pytest' in root.name or root.name in {'cao-spec008-repair-graph','cao-spec008-repair-graph-source','cao-spec008-repair-browser-libs'}: continue
    for name in ('public-results.json','result.json','browser-server.json','receipt-fault-events.jsonl','receipt-gate.json','live-opencode-capture.txt','live-codex-capture.txt','live-claude-capture.txt'):
        p=root/name
        if p.is_file(): save(p,Path(root.name)/name)
    for p in (root/'.aws/cli-agent-orchestrator/logs/terminal').glob('*.scrollback'):
        save(p,Path(root.name)/p.name)
manifest['policy']='Preserve hashes, sanitized logs/results and replay scripts; exclude credentials, complete native databases and raw ANSI terminal streams. Active evidence snapshots remain provisional until the controller closes.'
manifest_path.write_text(json.dumps(manifest,indent=2))
print('Archived evidence files:',len(manifest['files']))
