# T093 — Collapsed native headers between rendered parts

Date: 2026-10-23. Status: correction verified offline; fresh T094 acceptance pending.

T092 exposed a liveness regression in the first T091 correction. A source-derived native tool-summary→blank→complete collapsed Thought header→blank→Unicode/receipt frame raises the same interleaved-reasoning rejection under1d46ccbc. Native tool rows and intermediate public rows are not all final TextPart boundaries. The pinned renderer gives each Reasoning/Text/Tool part its own layout.

The corrected one-pass filter removes only full native collapsed-header rows at blank part boundaries inside a candidate already witnessed by the active receipt. It preserves all other rows in order and preserves existing blank lines. Expanded, incomplete-reserved and unseparated headers still fail closed. It never selects a final paragraph or the last ACK, and leaves the strict result, counters, Unicode, ACK, deadline, permission and cleanup gates unchanged.

The earlier interleaved-collapsed rejection test embodied the invalid assumption. Its fail-closed coverage is retained as interleaved-expanded rejection, and three positive tests now cover tool summary, ordinary public text and a tool-icon row before a collapsed header, asserting all public text survives.

Final focused provider/receipt/service/recovery selection:208tests PASS,4warnings,91.57s, configured coverage retained. Independent Claude read review `aa21551997db48a9b7a80245eb15b87f` found no blocker and verified the new part-boundary logic. Existing multiple collapsed-header, receipt-only, fence, generic prose/bullet, stale/unbound and expanded-layout controls remain. Composition passes five contracts, zero broken.

Reserved native chrome limits remain explicit: an unquoted complete native-header row at a native part boundary cannot be distinguished from deliberately identical text; exact bare + Thought/- Thought rows are also reserved fail-closed. Ordinary Thought sentences/bullets with content and quoted/fenced text are preserved. Unknown/truncated nonmatching headers remain outside the accepted complete-header grammar. No universal native reasoning-channel guarantee is claimed.

Composition verdict: PASS for the corrected parser→active-receipt→durable public-result boundary. No new module dependency, API/schema or permission changes; existing Graphify relationships were source-verified and foreign graph files preserved. Fresh T094 generation and managed acceptance must bind the corrective commit, not reuse T092's source pins.
