"""Read-only differential checks. Imports current code; compiles selected pure branch definitions."""
import ast, asyncio, logging, subprocess, types
from cli_agent_orchestrator.utils import tool_mapping
from cli_agent_orchestrator.graph.cache import GraphViewCache
from cao_workflow.exceptions import ShimHTTPError
from unittest.mock import patch

def branch_file(ref, path):
 return subprocess.check_output(['git','show',f'{ref}:{path}'],text=True)

def pure_definition(ref, path, name, namespace):
 tree=ast.parse(branch_file(ref,path))
 node=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name==name)
 exec(compile(ast.Module(body=[node],type_ignores=[]),path,'exec'),namespace)
 return namespace[name]

with patch.object(tool_mapping,'_get_role_defaults',return_value=None):
 current=tool_mapping.resolve_allowed_tools(None,'audit_role_does_not_exist')
print('CURRENT unknown role:',current)
namespace={'logger':logging.getLogger('audit'),'List':list,'_get_role_defaults':lambda role:None}
resolve=pure_definition('a1c3d138c38e58d1df04f9850b828e0b3df513e6','src/cli_agent_orchestrator/utils/tool_mapping.py','resolve_allowed_tools',namespace)
try: resolve(None,'audit_role_does_not_exist')
except ValueError: print('UPSTREAM unknown role: ValueError (refused)')
print('CURRENT explicit allowedTools empty + MCP:',tool_mapping.resolve_allowed_tools([],None,['cao-mcp-server']))
print('UPSTREAM explicit allowedTools empty + MCP:',resolve([],None,['cao-mcp-server']))
body='{"detail":{"kind":"decision_required","message":"review existing attempt"}}'
print('CURRENT workflow refusal:',str(ShimHTTPError(409,body)))
source=branch_file('a1c3d138c38e58d1df04f9850b828e0b3df513e6','src/cao_workflow/exceptions.py')
ns={'__name__':'audit_shim'}
exec(compile(source,'branch_exceptions.py','exec'),ns)
print('UPSTREAM workflow refusal:',str(ns['ShimHTTPError'](409,body)))

async def check_cache():
 cache=GraphViewCache();started=asyncio.Event();released=asyncio.Event();calls=0;cancellations=0
 async def builder():
  nonlocal calls,cancellations
  calls+=1;started.set()
  try: await released.wait()
  except asyncio.CancelledError: cancellations+=1;raise
  return {'synthetic':'view'}
 task=asyncio.create_task(cache.get_or_build(('audit',),builder))
 await started.wait();task.cancel()
 try: await task
 except asyncio.CancelledError: pass
 retry=asyncio.create_task(cache.get_or_build(('audit',),builder))
 await asyncio.sleep(0);released.set();await retry
 print('CURRENT graph caller cancelled: builds=',calls,'cancelled builds=',cancellations)
 print('CURRENT graph detached task API exists:',hasattr(cache,'get_or_build_task'))
asyncio.run(check_cache())
import re
from cli_agent_orchestrator.providers.opencode_cli import COMPLETION_MARKER_PATTERN
branch_tree=ast.parse(branch_file('a1c3d138c38e58d1df04f9850b828e0b3df513e6','src/cli_agent_orchestrator/providers/opencode_cli.py'))
branch_pattern=next(ast.literal_eval(n.value) for n in branch_tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='COMPLETION_MARKER_PATTERN' for t in n.targets))
frame='▣ Sisyphus - Ultraworker · model · 2s'
print('CURRENT OpenCode v1 spaced agent marker:',bool(re.search(COMPLETION_MARKER_PATTERN,frame)))
print('UPSTREAM OpenCode v1 spaced agent marker:',bool(re.search(branch_pattern,frame)))
