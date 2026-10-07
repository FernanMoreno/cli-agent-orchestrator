T066 catalog closure

Root supplied preimplementation Graphify query at /home/felni/tmp-cao015-root/catalog-graph-query.log and Python CI RED: test_every_click_command_has_a_catalog_row / test_the_declared_command_count_matches_the_click_tree (141 Click leaves vs132 catalog).

Independent regression-first proof: cargo test --locked --manifest-path tui/Cargo.toml --bin cao-tui unreviewed_peer_and_plugin_commands_stay_hidden_and_routeless failed with `cao peer accept must have a catalog row` before production edits. Log catalog-rust-red.txt.

Root cause: static catalog and exhaustive route classifier had not incorporated six peer leaves and three plugin approval/review leaves. Added nine enum variants, metadata rows, DISPLAY_ORDER membership, exhaustive independent completeness-test arms/list, and explicit None route arms. New metadata read directly from Click command objects (help first paragraph and params), never scraping help output; captured catalog-new-metadata.json. Count141, policy24 InApp/18 Handoff/99 Hidden. All nine remain Hidden, absent navigation, without routes; regression explicitly protects each condition.

Commands use TMPDIR=/home/felni/tmp-cao015-types-data and CARGO_BUILD_JOBS=1 with existing tui/target cache:
- .venv/bin/python -m pytest test/test_command_catalog_matches_click.py --no-cov -q: 4 passed27.76s (catalog-python-green.txt).
- /home/felni/.cargo/bin/cargo clippy --locked --manifest-path tui/Cargo.toml --all-targets -- -D warnings: exit0, finished8.89s (catalog-clippy.txt).
- /home/felni/.cargo/bin/cargo fmt --manifest-path tui/Cargo.toml --check: exit0.
- /home/felni/.cargo/bin/cargo test --locked --manifest-path tui/Cargo.toml --bins: 223passed30.04s initialgreen (catalog-rust-green.txt); finalsource rerun catalog-rust-final.txt.
- git diff --check -- tui/src/catalog.rs tui/src/server.rs: exit0.

No lib target exists, therefore unit scope uses --bins. Full endpoint integration requires root's existing isolated API wrapper and is delegated to root. Root owns architecture/composition/global validation. Only catalog.rs and server.rs changed for this task; existing dirty modifications retained. No persistence, routes, interactive scope, or execution behavior changed.

Root reviewed the nine additions against actual source and exact existing Hide policy; prior dirty catalog/router changes are preserved. Isolated real CAO API wrapper repeated after closure: endpoint4, hermeticity11, no-backend-attach5, no-colour-literal7 and PTY9 — **36 integration tests passed, exit0**. API profile/TMUX/temp roots isolated, authentication explicitly disabled and event-plugin loading stubbed; this verifies endpoint compatibility rather than authenticated-provider acceptance. Log `/home/felni/tmp-cao015-root/tui-catalog-integration.log`.
