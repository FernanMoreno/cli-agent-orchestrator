# Spec 015 — focused structural refresh

Historical snapshot before the final T077–T087 corrections. The hashes describe
the bytes verified at extraction, not the later publication candidate. Current
source/test hashes are in the [final source manifest](../../specs/015-local-audit-closure/final-source-manifest.json).

Deterministic Graphify AST extraction for 26 scoped source files. 1652 nodes; 3711 edges; zero failed sources. Input/output tokens: 0/0. Source SHA-256 values were unchanged before and after extraction and checked again at completion.

This supplements the preexisting full graph without overwriting it. No semantic extraction, full-repository coverage, behavioral correctness, community analysis or restart durability is claimed. Edges in graph.json retain extraction provenance; important implementation conclusions were checked against source and executable tests.

The manifest records file hashes and extraction scope. The JSON is a NetworkX node-link graph.
