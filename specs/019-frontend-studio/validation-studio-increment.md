# Studio navigation and execution-observation increment

Date: 2026-10-21. Scope: the first observation-only surface from spec 017, grouped navigation and explicit current context from spec 019, plus bearer-revocation correctness. This does not complete either specification or implement spec 020.

## Source and regression evidence

Claude supplied the isolated frontend worktree; root reviewed and integrated its changes into `main`, preserving the existing backend changes. Independent reviews found auth and mobile-layout defects, which root reproduced and corrected before publication.

- Complete web suite after the auth corrections: **531 passed, 48 modules**, 187.20 s reported by Vitest; TypeScript and production build also passed. The source hashes stayed unchanged during those checks.
- The only subsequent source delta adds wrapping and width constraints at four layout sites in `App.tsx` and `StudioNav.tsx`. The final focused suite passed **44 tests in three modules**, TypeScript passed and production build passed. No assertions, coverage thresholds, memory floors or original compatibility tests were removed.
- Registered project composition check on the integrated source: **five contracts kept, zero broken**, 410 Python files and 1,828 dependencies. This is an architecture check, not provider acceptance or global coverage.
- Independent source review approved the auth boundary and final CSS changes. The final layout change preserves DOM order, tab semantics, manual activation, shortcut mapping and the navigation lock.

Private receipts are retained under `frontend-root-review-r4`: `full-web-parallel-r3-frj4zzwn/receipt.json` and `web-layout-final-r4-hhakf5_5/receipt.json`. Earlier interrupted and failing runs remain retained, including full-suite manual/bootstrap compatibility failures and the delayed-body regression.

## Browser scope

The browser check serves the actual production bundle using private Chromium 153 / Playwright 1.63.0 and explicit local API mocks. It does not contact a CAO backend or provider and cannot prove authorization, live updates or native operation. Chromium and its missing ALSA runtime library were downloaded/extracted into a private cache; no global packages or credentials were changed.

The initial harness assumed shortcut order equalled the newly grouped visual order; that assertion was corrected to the actual DOM order while preserving manual activation and shortcut checks. A subsequent run reproduced genuine horizontal overflow at 375 px (document/body 546 px). The correction wraps the Work group and header controls instead of hiding overflow. The first post-correction run passed both viewport checks but exposed a missing local Tasks-capabilities fixture; the final harness explicitly supplies the source-contract-shaped disabled capability response and still rejects every unmapped API request.

Final browser run **passed** all five groups: current context, grouped destinations, manual keyboard activation and shortcuts, empty execution snapshot/coverage, and desktop/mobile width. At 375 px the document and body are both 375 px. Unmapped API calls, external HTTP/WebSocket attempts, unexpected console errors, page errors and static-resource errors were all zero. The single browser resource diagnostic from the deliberately mocked `/api/fleet` 404 is recorded separately, matched by its exact local URL and 404 error shape; the application fallback remains exercised. Browser-process and static-server cleanup were confirmed.

Final private receipt: `browser-smoke-r8/run-A6VcK3/receipt.json`, SHA-256 `9122920360bb9d939ad964ebcaae64851919629a0fc276cd69d7bbb555549e8c`. Final 119-file source fingerprint: `8b1285852d66a5b14cee4e204a77f12952924f97a6003d687ec5a1a828951772`. Earlier harness/environment failures remain retained. This is a bounded production-bundle render with mocks, not a real backend acceptance test.

## Remaining acceptance

Screen-reader/APG acceptance, the spec 017 canvas and live journal/cursors, metadata-only server reads and ACL projection, complete spec 019 flows, Spanish localization, spec 020 backend contracts, multi-node scale and all native/global gates remain open. The context bar reports the access method and unknown permissions; it does not invent a bearer principal or permission summary.
