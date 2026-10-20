# Requirements Quality Checklist: Work Agent Messages

- [x] User stories identify sender, receiver, operator, and measurable outcomes.
- [x] The message tool is explicitly distinct from generic `send_message`.
- [x] Sender and receiver identities are bounded by server-authenticated attempt generations.
- [x] Cross-job access, permissions, stale generations, retries, and privacy are covered.
- [x] PostgreSQL durability and atomic event persistence are explicit.
- [x] API replacement and AG-UI projection have independently testable scenarios.
- [x] Existing provider message and question/answer behavior is protected.
- [x] Unsupported provider-backed Work identity is called out rather than assumed.
- [ ] Source inspection confirms the receiver contract permission can be revalidated without introducing a new trust source.
- [ ] Migration and API/AG-UI composition tests pass.
