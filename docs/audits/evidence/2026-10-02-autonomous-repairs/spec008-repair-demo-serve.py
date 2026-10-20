import importlib.util,json,os
from pathlib import Path
from http.server import ThreadingHTTPServer
root=Path(Path('/tmp/spec008-repair-demo-pointer').read_text());project=Path('/mnt/c/users/ferna/onedrive/escritorio/cao-turn-recovery-demo-20261002-f9086a25');spec=importlib.util.spec_from_file_location('demo_browser_actual',project/'api/server.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);data=root/'browser-fixture';data.mkdir(exist_ok=True);m.NOTES_FILE=data/'notes.json';m.NOTES_FILE.write_text('[]\n');m.FRONTEND=project/'frontend'
server=ThreadingHTTPServer(('127.0.0.1',0),m.Handler);(root/'browser-server.json').write_text(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'project':str(project),'data':str(m.NOTES_FILE),'pid':os.getpid()}));print('Browser fixture ready',server.server_port,flush=True)
try:server.serve_forever()
finally:server.server_close()
