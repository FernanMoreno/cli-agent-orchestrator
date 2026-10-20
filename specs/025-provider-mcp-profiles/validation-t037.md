# T037 — pinned real OpenCode v1 acceptance (in progress)

## OC1-01 / T039 completed preparation

The installed `opencode` is v2.0.18; a private copy and `--version` check confirmed that it is not a v1 acceptance target. The official v1.18.35 Linux x64 baseline asset was then downloaded into a separate owner-only directory. Its archive SHA-256 matched the official GitHub asset digest, extraction copied exactly one regular executable without applying archive paths/links, and its private `--version` returned exactly `1.18.35`. The executable SHA and the official tagged upstream commit are recorded in [the public pin](opencode-v1-pin.json). No global executable, login or credential file was changed. The private pin/version receipt remains in the existing evidence cache. This is preparation, not a model turn or provider acceptance.

Primary provenance: [official v1.18.35 release](https://github.com/anomalyco/opencode/releases/tag/v1.18.35), checked against [release metadata](https://api.github.com/repos/anomalyco/opencode/releases/tags/v1.18.35). Source is pinned to commit `53d1eabb61e21162157817bf677da0a4ad3332e3`, not a moving branch.

## Required correction before real runs

The current CAO v1 command still points at shared installed config/agent directories and inherits operator data/cache/home locations; it also explicitly refuses managed tool-policy envelopes. Therefore this binary must not be used to infer isolated profile acceptance through the current route. T040 will add/test a private v1 launch before any model/authentication operation. Source inspection of the pinned release confirms native `permission` syntax; v2 `permissions` arrays are explicitly rejected by its compatibility reader. Simply reusing v2 configuration would not prove safe propagation.

The pinned source also confirms `XDG_DATA_HOME` for auth.json, XDG configuration/cache/state paths, `OPENCODE_TEST_HOME` for home scanning, the project-config disable flag and late config/permission/auth-content overrides. These boundaries require explicit isolation rather than assuming OPENCODE_CONFIG alone excludes global/project settings. No production correction is claimed by this document.

## Resource and acceptance limits

Memory fell below 6,144 MiB after the scoped implementation checks. No intensive real provider test was started. Download/extraction and the bounded private `--version` probe used no model, PostgreSQL, operator credentials or historical job. Remaining OC1-02–05, T037/T040, v2 source-bound/catalog/auth, separate message contracts and concurrency gates remain open. Deferred T032 and global NFR/coverage status are unchanged. Codex only; no Claude or delegated agent was used.
