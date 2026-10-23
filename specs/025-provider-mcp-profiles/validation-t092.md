# T092 — First actual after T091

Date: 2026-10-23. Actual source `1d46ccbc`. Status: FAILED; this immutable attempt is superseded by the T093 correction and planned T094 revalidation, not relabelled PASS.

Fresh generation passed14.585s with8,754MiB, natural exit0, Unicode/event/HTTP checks and complete owned-process/private-state cleanup. Operator/source/binary/runner unchanged. Receipt SHA `de17d004c31007ccc7055a4f49a358a81d791ef1d180565d6c3633298f1e5768`.

The reviewed managed runner retained17helper ASTs and60ACK controls, with only current HEAD/source pins rebound. Executed SHA `ff70c917ad8dfd0746f507e2e8967ccc90aaf4fc221127044553e98b17281d36`. It FAILED model_receipt_not_observed in132.675s, phase0. Discovery and message operations completed, while the sender reached reconcile after three verification attempts. The bounded stateless viewport extractor still observed the literal Unicode marker and one standalone receipt in365characters; the bound public extraction did not complete. No receiver ACK/restart acceptance occurred, and native-text probes did not fire.

Original-run API/capture/tmux/PostgreSQL/private-state cleanup and operator/source/binary/runner/HEAD preservation all passed. Executed bytes preserved; gate reclosed.

Root reproduced the newly introduced leading-only/interleaved-header rejection on a valid native tool-summary→collapsed-header→TEXT layout. Earlier rows were incorrectly assumed to be TextParts. T093 corrects that adapter boundary by removing only full separated native collapsed-header rows wherever rendered parts place them, preserving every other row and every acceptance gate. The failed T092 remains evidence of the regression rather than evidence against the model.
