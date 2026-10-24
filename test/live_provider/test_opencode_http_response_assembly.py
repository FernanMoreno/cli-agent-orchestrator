"""Actual private JS installation/hooks/ledger composed with the strict reader."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from test.live_provider import opencode_http_prompt_observer as prompt
from test.live_provider import opencode_http_response_argument_observer as response

import pytest

NONCE = "0123456789abcdef0123456789abcdef"
ROOT = Path(__file__).resolve().parent
MESSAGES = ["PRIVATE_BODY_ONE: café, español y 日本語.", "PRIVATE_BODY_TWO: sesión continúa."]
PROMPTS = ["source phase one LAST", "source phase two LAST"]
DOCUMENT = {
    "schema_version": 1,
    "nonce": NONCE,
    "phase_prompts": PROMPTS,
    "receipt_prefix": "\nreceipt: ",
    "receipt_suffix": "\nLAST",
    "last_instruction": "LAST",
}

DRIVER = r"""
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const [work,nonce,mode] = process.argv.slice(1);
const dir=work+'/.opencode';
const expectation=JSON.parse(await fs.readFile(dir+'/http-response-argument-expectations.json','utf8'));
const prompts=JSON.parse(await fs.readFile(dir+'/http-prompt-expectations.json','utf8'));
const module=await import(pathToFileURL(dir+'/plugins/cao-http-response-argument-observer.js').href);
const hooks=new Map();
const cleanup=await module.default.setup({session:{hook:async(name,fn)=>{
  hooks.set(name,fn);return {dispose:async()=>{}};
}}});
if(typeof cleanup!=='function'||hooks.size!==2)throw new Error('observer_not_started');
for(const phase of (['exact','sse','sse_no_choice_index'].includes(mode)?[1,2]:[1])){
  const body=JSON.stringify({messages:[{role:'user',content:prompts.phase_prompts[phase-1]+
    prompts.receipt_prefix+'CAO-TURN-RECEIPT-'+'b'.repeat(32)+prompts.receipt_suffix}]});
  const request=new Request('https://example.invalid/PRIVATE_URL',{method:'POST',body,
    headers:{Authorization:'Bearer PRIVATE_HEADER'}});
  const event={kind:'primary',model:{providerID:'opencode-go',id:'glm-5.3-flash'},request};
  if(hooks.get('http.request')(event)!==undefined)throw new Error('request_callback_blocked');
  if(await request.text()!==body)throw new Error('request_changed');
  const original=expectation.phase_messages[phase-1];
  const message=mode==='variant'?original.slice(0,-1)+'。':original;
  const call={
    id:'PRIVATE_CALL_ID',type:'function',function:{name:'cao-mcp-server_send_message',
      arguments:JSON.stringify({message,receiver_id:'PRIVATE_RECEIVER'})}
  };
  const wire=mode==='bad_sse'?
    'data: '+JSON.stringify({choices:[{index:0,delta:7,finish_reason:'stop'}]})+'\n\n':mode.startsWith('sse')?
    'data: '+JSON.stringify({choices:[{...(mode==='sse_no_choice_index'?{}:{index:0}),
      delta:{tool_calls:[{index:0,...call}]}}]})+'\n\n'+
    'data: '+JSON.stringify({choices:[{...(mode==='sse_no_choice_index'?{}:{index:0}),
      delta:null,finish_reason:'tool_calls'}]})+'\n\n'+
    'data: [DONE]\n\n':JSON.stringify({choices:[{message:{role:'assistant',tool_calls:[call]},
      finish_reason:'tool_calls'}]});
  const result=new Response(wire,{headers:{'content-type':(mode.startsWith('sse')||mode==='bad_sse')?
    'text/event-stream':'application/json'}});
  const actual=mode==='identity'?new Request('https://example.invalid/OTHER',{method:'POST',body}):request;
  if(hooks.get('http.response')({...event,request:actual,response:result})!==undefined)
    throw new Error('response_callback_blocked');
  if(await result.text()!==wire)throw new Error('response_changed');
}
await cleanup();
"""


@pytest.mark.parametrize(
    "mode", ["exact", "variant", "identity", "sse", "bad_sse", "sse_no_choice_index"]
)
def test_private_installed_js_hooks_and_strict_python_reader(
    tmp_path: Path, mode: str, monkeypatch
) -> None:
    runner_path = (
        ROOT.parents[1] / "specs/025-provider-mcp-profiles/fixtures/run_native_v2_acceptance.py"
    )
    spec = importlib.util.spec_from_file_location("t128_assembly_runner", runner_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    work = tmp_path / "sender-workdir"
    work.mkdir(mode=0o700)
    os.chmod(tmp_path, 0o700)
    old_source = (ROOT / "opencode_http_prompt_observer.js").read_bytes()
    old_source = module._http_prompt_plugin_bytes(old_source, nonce=NONCE)
    derived = tmp_path / "derived-prompt.js"
    derived.write_bytes(old_source)
    derived.chmod(0o600)
    prompt.install_project_plugin(work, derived, NONCE)
    prompt.write_expectations(work, DOCUMENT)
    ledger = response.install_project_plugin(
        work,
        ROOT / "opencode_http_response_argument_observer.js",
        ROOT / "opencode_http_response_protocol.js",
        NONCE,
    )
    response.write_expectations(
        work, {"schema_version": 1, "nonce": NONCE, "phase_messages": MESSAGES}
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", DRIVER, str(work), NONCE, mode],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    diagnostic = response.read_observation(ledger, NONCE)
    serialized = json.dumps(diagnostic, ensure_ascii=False)
    for private in [
        *MESSAGES,
        NONCE,
        "PRIVATE_RECEIVER",
        "PRIVATE_CALL_ID",
        "PRIVATE_HEADER",
        "PRIVATE_URL",
    ]:
        assert private not in serialized
    first, second = diagnostic["phase_results"]
    if mode in {"exact", "sse", "sse_no_choice_index"}:
        assert diagnostic["trace_complete"] is True
        assert first["status"] == second["status"] == "exact"
    elif mode == "variant":
        assert diagnostic["trace_complete"] is True
        assert first["status"] == "known_punctuation_variant"
        assert first["argument_exact"] is False
        assert first["known_punctuation_variant_exact"] is True
        assert second["status"] == "not_observed"
    elif mode == "bad_sse":
        assert diagnostic["trace_complete"] is True
        assert first["status"] == "unavailable"
        assert first["argument_exact"] is None
        assert diagnostic["records"][0]["reason"] == "sse_delta"
    else:
        assert diagnostic["trace_complete"] is False
        assert first["argument_exact"] is None

    # Continue through the actual runner collector after the Node writer exits.
    # This verifies its private snapshot layout against real emitted records.
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    raw = evidence / "raw"
    raw.mkdir(mode=0o700)
    source_paths = [
        "test/live_provider/opencode_http_prompt_observer.js",
        "test/live_provider/opencode_http_response_argument_observer.js",
        "test/live_provider/opencode_http_response_argument_observer.py",
        "test/live_provider/opencode_http_response_protocol.js",
    ]
    monkeypatch.setattr(module, "EVIDENCE", evidence)
    monkeypatch.setattr(module, "_ACTIVE_PRIVATE_RUN_ROOT", tmp_path)
    monkeypatch.setattr(
        module,
        "RUN_MANIFEST",
        {
            "source_sha256s": {
                relative: hashlib.sha256((module.REPO / relative).read_bytes()).hexdigest()
                for relative in source_paths
            }
        },
    )
    opencode = work / ".opencode"
    context = {
        "working_directory": work,
        "directory": ledger,
        "nonce": NONCE,
        "plugin_bytes": (opencode / "plugins" / response.PLUGIN_NAME).read_bytes(),
        "protocol_bytes": (
            opencode / response.SUPPORT_DIRECTORY_NAME / response.PROTOCOL_NAME
        ).read_bytes(),
        "prompt_plugin_bytes": old_source,
        "prompt_expectation_bytes": (opencode / "http-prompt-expectations.json").read_bytes(),
        "expectation_bytes": (opencode / "http-response-argument-expectations.json").read_bytes(),
    }
    collected = module._capture_http_response_argument_diagnostic_safely(
        context=context,
        root=tmp_path,
        raw_dir=raw,
        writers_dead=True,
    )
    assert collected["status"] == "sealed_observation_available", collected["reason"]
    assert collected["sealed_raw_copy_verified"] is True
    assert collected["observation"]["phase_results"] == diagnostic["phase_results"]
    assert "records" not in collected["observation"]
    public = json.dumps(collected, ensure_ascii=False)
    for private in [
        *MESSAGES,
        NONCE,
        "PRIVATE_RECEIVER",
        "PRIVATE_CALL_ID",
        "PRIVATE_HEADER",
        "PRIVATE_URL",
    ]:
        assert private not in public
