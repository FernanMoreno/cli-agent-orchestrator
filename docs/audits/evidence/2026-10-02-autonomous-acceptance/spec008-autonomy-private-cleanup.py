import json,sqlite3,os
from pathlib import Path
roots=[Path('/var/tmp/cao-spec008-autonomy-mqwb6edp'),Path(Path('/tmp/spec008-autonomy-pointer').read_text()),Path(Path('/tmp/spec008-autonomy-late-pointer').read_text())]
for root in roots:
 assert root.parent==Path('/var/tmp') and root.name.startswith('cao-spec008-autonomy-')
 data=json.loads((root/'public-results.json').read_text())
 if not data.get('ended_at') and root.name!='cao-spec008-autonomy-mqwb6edp':print(root.name,'still active');continue
 active=[]
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit():continue
  try:
   env=(proc/'environ').read_bytes().split(b'\0');comm=(proc/'comm').read_text().strip()
   if ('HOME='+str(root)).encode() in env and comm!='tmux: server':active.append({'pid':int(proc.name),'comm':comm})
  except (OSError,PermissionError):pass
 if active:print(root.name,'active fixture processes',active);continue
 db=root/'.local/share/opencode/opencode.db';counts={}
 if db.exists():
  c=sqlite3.connect(db);c.execute('pragma secure_delete=on');tables={x[0] for x in c.execute("select name from sqlite_master where type='table'")}
  for name in ['account_state','account','control_account','credential']:
   if name in tables:
    counts[name]=c.execute('select count(*) from '+name).fetchone()[0];c.execute('delete from '+name)
  c.commit();c.execute('pragma wal_checkpoint(truncate)');c.execute('vacuum');c.close()
 for name in ['.codex/auth.json','.claude/.credentials.json','.claude.json','.local/share/opencode/auth.json']:(root/name).unlink(missing_ok=True)
 result={'fixture':str(root),'credential_tables_purged':counts,'copied_auth_files_removed':True,'personal_credentials_untouched':True};(root/'credential-cleanup.json').write_text(json.dumps(result,indent=2));print(root.name,'credential cleanup completed',counts)
