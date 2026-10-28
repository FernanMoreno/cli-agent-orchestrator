# T207 · physical MCP EOF guard

Status: dormant implementation reviewed and final regression passed; current
native direct-MCP preflight pending. Earlier review findings were corrected. The opt-in is dormant:
`CAO_INTERNAL_MCP_PHYSICAL_EOF=1`. No provider default has changed, and T197
native automatic message/restart acceptance remains open.

## Reproduced boundary and implementation

The installed MCP SDK constructs UTF8 text wrappers over `sys.std*.buffer`.
Observing only the original text methods misses its actual transport. A public
FastMCP lifespan now optionally exposes observed binary pipes, preserving bytes
and original stream/descriptor/signal ownership. The guard tracks JSONRPC IDs
in both directions until successful output flush. Actual kernel stdin EOF and
settled framing are mandatory for normal return; signals never synthesize EOF.
Unknown inbound notifications execute but prevent a normal completion claim.

Runtime admission is limited to Linux with FastMCP3.2.0, MCP1.28.1 and
AnyIO4.15.1. Absent opt-in performs no runtime version admission. One event-loop
watchdog, an owned Linux interval timer across complete `mcp.run()` teardown,
and small wake pipes use the SDK's existing IO workers; no additional
persistent process or thread is introduced.

The first installed-SDK regression had20passing cases and one failed busy case:
an active SDK handler remained shielded after actual stdin EOF and exceeded the
unchanged12s test wait. This failure is preserved privately. First physical EOF
now starts the same fixed6s deadline as a first stop signal. Expiry exits the
owned MCP process with failure70, without returning from `mcp.run()` or producing
a normal audit seal. Later signals do not reset the deadline. This is bounded
failure handling, not acceptance of an unfinished operation.

An independent read review identified default-executor shutdown outside the
lifespan. A new actual SDK control cancelled `asyncio.to_thread(time.sleep,30)`,
received its tool reply, then closed stdin: the old implementation still exceeded
the unchanged12s test wait. The failure is preserved. The correction keeps the
same first-stop/EOF6s deadline alive across `mcp.run()` and `Runner.close()` using
SIGALRM/ITIMER_REAL. Expiry is failure70, with no normal server-return observer.
An existing interval timer is refused; genuine normal return restores prior
SIGALRM and timer ownership. No persistent timer thread is added.

A first correction selection passed30cases in262.63s, including the cancelled
executor thread. Two constructor-finalization warnings exposed incomplete
initialization before non-pipe refusal; the constructor cleanup was corrected,
and the final regression treats those warnings as errors. Fatal frame errors
now start the same bounded close even without physical EOF; an expired callback
fails immediately instead of losing timer admission through an event-loop log.
Final selection:50PASS in343.37s with unraisable-finalizer warnings treated as
errors. No warnings remained. Current mypy (two production files), Black/isort
(four files), diff whitespace and composition (5contracts kept/0broken) pass.
Final scoped source review is GO; current native evidence remains pending.

## Verification and composition review

- Initial missing-module RED preserved; first13pure/FD cases passed.
- Initial selection before executor correction:41PASS in205.55s across physical state/FD tests, installed SDK
  subprocess controls, production server startup, tool profiles and readiness.
- SDK controls include actual EOF, repeated signals then EOF, signal without
  EOF, busy handler, malformed/partial input, unknown notification and the real
  CAO server. Failure cases produce no normal marker.
- Initial Black and isort:4files unchanged. Initial Mypy checks both production files, including
  untyped bodies:PASS. Architecture:5contracts kept,0broken.
- Boundaries reviewed: installed SDK buffer creation, wire response versus
  memory-stream completion, worker-thread state locking, flush ordering,
  cancellation, six-second deadline, handler/stream/FD restoration, opt-in
  environment and production FastMCP startup. No database schema, HTTP contract
  or external effect authority changes. The new transport tests use real local
  processes/pipes/signals rather than model or database doubles. The selection
  also includes the existing readiness fixture; the entire selection is not
  claimed database-free.
- Graphify output is preserved; source inspection verifies the public lifespan
  and SDK buffer path. No graph regeneration is needed for native evidence.

Private native preflight uses the real production builder and server, an empty
private HOME/config, dummy key, credential masks and a verified no-egress
namespace. It requests zero model turns and no API/PostgreSQL resources. Its
acceptance requires genuine SDK normal return, real physical EOF, one MCP
launch, native CLIexit0 and positive retirement of the exact owned MCP identity
within the original shared6s budget. No proxy or harness EOF marker is involved.

## Remaining acceptance

Run the reviewed, committed-source-bound native preflight and preserve its
actual result. Only a positive current result can justify client-default
activation, which then requires its own environment/compatibility tests and
full original T197 journey. Wire settlement certifies protocol closure, not
completion of arbitrary external effects after an RPC response.

Independent review: Claude diagnosis105ca51e, implementation reviewbf37cad5,
executor correction reviewcae49fbb and final scoped correction98b59fb7 (GO).
The additional correction review followed concrete executor RED/progress; it did
not rerun a failing native case. Final review confirms fatal-frame/no-EOF timer
admission, expiry fail-closed behavior and constructor cleanup. Native signal
sender/status remains unobserved unless separately established by real evidence.

Private controller preflight passes actual import statements, the real provider
builder and guard environment, two native-control-shaped pipe observations,
exclusive writers/strict readers and the normal observer signature. It starts
no native CLI, service or login operation. Descriptor is closed until a commit
and independent immutable execution archive bind its actual bytes and sources.
