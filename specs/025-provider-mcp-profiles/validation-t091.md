# T091 — Receipt-bound v1 collapsed Thought chrome

Date: 2026-10-23. Status: implementation and focused verification; fresh native acceptance pending.

T090 proved the receiver native TEXT is exactly ACK while public LAST contains20characters in3lines. Pinned v1.18.35 source shows hide mode still emits a completed collapsed ReasoningHeader, e.g. + Thought: 1.2s. That15-character row plus a blank line plusACK reproduces the observed20/3shape through the uncorrected extractor. T090 did not retain the literal extra row; source-level reproduction and actual byte-shape evidence must remain distinct.

[Native header](https://github.com/anomalyco/opencode/blob/53d1eabb61e21162157817bf677da0a4ad3332e3/packages/tui/src/routes/session/index.tsx) and [duration grammar](https://github.com/anomalyco/opencode/blob/53d1eabb61e21162157817bf677da0a4ad3332e3/packages/tui/src/util/locale.ts) explain the completed/collapsed forms. Pinning thinking_mode=hide alone would not remove the header; the correction belongs at extraction.

The v1 extractor now removes only complete leading collapsed-header blocks whose duration matches that grammar, after the active standalone receipt has been witnessed. It preserves text/receipt content and requires a non-receipt public row after the header. Expanded/interleaved recognized native chrome and missing header separation fail closed. Stale/unbound receipts remain unchanged; quoted/fenced/ordinary prose and markdown bullets are preserved. V2, model, prompts, permissions and acceptance predicates are unchanged. FULL diagnostic output is unchanged.

Literal unquoted first rows identical to native chrome remain reserved framing; the TUI supplies no independent distinction. Unknown/truncated title rows lacking complete duration grammar are not silently removed. This is a bounded collapsed-header correction, not universal parsing of every reasoning layout.

Initial18cases:14failed/4passed before correction. Independent read reviews identified ordinary Thought prose and markdown bullets rejected by an overly broad interleaved check; root reproduced and corrected both, and added regression cases. Mixed fence delimiters now preserve code content, and header+receipt without public text is rejected. Final parser/service selection and composition results are recorded after completion below.

Composition surface: provider extraction→active-receipt witness→durable public-result cache. Existing service get_output catches extractor ValueError before _verify_receipt_bearing_result, so rejected ambiguity cannot settle a receipt; current service/recovery checks cover those boundaries. Graphify's existing extractor/BaseProvider/service relationships were verified against source; no new module dependency, API/schema, permission or persistence contract. Existing foreign graph artifacts remain preserved.


## Final offline verification

Final unchanged selection of five provider/receipt/service/recovery modules:205tests PASS,4warnings,81.22s with configured coverage retained. Diagnostic fixture22tests PASS; existing mandatory ACK controls60PASS. Formatting/checks passed. Composition:412files/1,834dependencies, five contracts kept, zero broken.

Claude read review `05531a7f693c44a5bcf4f3f05279c0a8` requested a narrower bullet grammar. Root reproduced both valid ordinary bullet failures and applied that exact narrowing; the final combined run includes their regressions. No extra speculative provider change was made. Final diff preserves all old acceptance rules; latest source needs new pins and fresh generation/managed acceptance (T092). The code correction is integrated separately from any live acceptance claim.

Composition verdict: PASS for the bounded extractor correction and its provider/service/recovery scenarios; native-v1 acceptance remains pending until T092. The previous raw-frame literal cause was not directly inspected; the source-proven collapsed-header defect has executable RED/GREEN evidence and will be judged by the unchanged native gates.
