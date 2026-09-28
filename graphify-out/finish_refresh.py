"""Verified local Graphify refresh; modifies only generated graph artifacts."""
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / 'graphify-out'
SPEC = '/home/felni/.codex/skills/graphify/references/extraction-spec.md'


def read(name):
    return json.loads((OUT / name).read_text())


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2))


def validate():
    manifest = read('refresh-chunks/manifest.json')
    merged = dict(nodes=[], edges=[], hyperedges=[], input_tokens=0, output_tokens=0)
    seen = set()
    for entry in manifest:
        fragment = json.loads(Path(entry['output']).read_text())
        files = set(entry['files'])
        ids = {n['id'] for n in fragment['nodes']}
        covered = {n['source_file'] for n in fragment['nodes']}
        assert files <= covered, ('uncovered semantic files', files - covered)
        for node in fragment['nodes']:
            assert re.fullmatch('[a-z0-9_]+', node['id']), node
            assert node['file_type'] in {'code', 'document', 'paper', 'image', 'rationale', 'concept'}, node
            assert node['source_file'] in files, node
            assert Path(node['source_file']).is_file(), node
            assert node['id'] not in seen, ('duplicate semantic ID', node['id'])
            seen.add(node['id'])
        for edge in fragment['edges']:
            assert edge['source'] in ids and edge['target'] in ids, edge
            assert edge['source_file'] in files, edge
            confidence = edge['confidence']
            score = edge['confidence_score']
            assert confidence in {'EXTRACTED', 'INFERRED', 'AMBIGUOUS'}, edge
            assert (confidence == 'EXTRACTED' and score == 1.0) or (confidence == 'INFERRED' and score in {.95, .85, .75, .65, .55}) or (confidence == 'AMBIGUOUS' and .1 <= score <= .3), edge
        for h in fragment.get('hyperedges', []):
            assert set(h['nodes']) <= ids and h['source_file'] in files, h
        for key in ('nodes', 'edges', 'hyperedges'):
            merged[key].extend(fragment.get(key, []))
    cached = read('.graphify_cached.json')
    for key in ('nodes', 'edges', 'hyperedges'):
        merged[key].extend(cached.get(key, []))
    write('.graphify_semantic.json', merged)
    print('SEMANTIC_VALID', len(manifest), 'chunks', len(merged['nodes']), 'nodes', len(merged['edges']), 'edges', flush=True)
    return merged


def build():
    from graphify.build import build_merge
    from graphify.cluster import cluster, score_all
    from graphify.analyze import god_nodes, surprising_connections
    from graphify.diagnostics import diagnose_extraction, format_diagnostic_report
    semantic = validate()
    ast = read('.graphify_ast.json')
    fresh = {key: ast.get(key, []) + semantic.get(key, []) for key in ('nodes', 'edges', 'hyperedges')}
    fresh.update(input_tokens=0, output_tokens=0)
    write('.graphify_fresh.json', fresh)
    old = read('graph.before-refresh-20260922.json')
    old_sources = {n.get('source_file') for n in old['nodes']} - {None, ''}
    # source_file may denote an external module, not a repository file.
    # Only recognizable file paths can be candidates for deletion pruning.
    detection = read('.graphify_detect.json')
    suffixes = {Path(p).suffix.lower() for fs in detection['files'].values() for p in fs} - {''}
    deleted = sorted(p for p in old_sources if Path(p).suffix.lower() in suffixes and not (ROOT / p).exists())
    assert not set(deleted) & {'@playwright/test', 'graphology', 'tailwindcss'}
    write('refresh-deleted-sources.json', deleted)
    print('MERGE_START', len(fresh['nodes']), len(fresh['edges']), 'deleted sources', len(deleted), flush=True)
    graph = build_merge([fresh], graph_path=OUT / 'graph.before-refresh-20260922.json', prune_sources=deleted or None, root=ROOT, directed=old.get('directed', False))
    assert graph.number_of_nodes() > 0
    extraction = {
        'nodes': [{'id': n, **d} for n, d in graph.nodes(data=True)],
        'edges': [{**{k: v for k, v in d.items() if k not in ('_src', '_tgt', 'source', 'target')}, 'source': d.get('_src', u), 'target': d.get('_tgt', v)} for u, v, d in graph.edges(data=True)],
        'hyperedges': list(graph.graph.get('hyperedges', [])),
        'input_tokens': 0, 'output_tokens': 0,
    }
    write('.graphify_extract.json', extraction)
    # Some library normalization mutates its input; diagnose the saved raw data.
    health = diagnose_extraction(read('.graphify_fresh.json'), directed=graph.is_directed(), root=str(ROOT))
    write('refresh-health.json', health)
    print(format_diagnostic_report(health), flush=True)
    print('CLUSTER_START', graph.number_of_nodes(), graph.number_of_edges(), flush=True)
    communities = cluster(graph)
    cohesion = score_all(graph, communities)
    analysis = dict(communities=communities, cohesion=cohesion,
                    gods=god_nodes(graph, top_n=5),
                    surprises=surprising_connections(graph, communities, top_n=3))
    write('.graphify_analysis.json', analysis)
    overview = []
    for cid, nodes in communities.items():
        ranked = sorted(nodes, key=lambda n: graph.degree(n), reverse=True)
        sources = Counter(str(graph.nodes[n].get('source_file', '')).rsplit('/', 1)[0] for n in nodes)
        overview.append(dict(id=cid, size=len(nodes), labels=[graph.nodes[n].get('label', n) for n in ranked[:7]], directories=sources.most_common(3)))
    write('refresh-community-overview.json', overview)
    print('CLUSTERED', len(communities), 'communities; ready for source-grounded labels', flush=True)


def publish():
    from graphify.build import build_from_json
    from graphify.export import to_json
    from graphify.report import generate
    from graphify.analyze import suggest_questions
    from graphify.cache import save_semantic_cache
    from graphify.detect import save_manifest
    from graphify.cli import _stamped_manifest_files
    detection = read('.graphify_detect.json')
    scan_finished = (OUT / '.graphify_detect.json').stat().st_mtime_ns
    changed_during_refresh = [p for fs in detection['files'].values() for p in fs if Path(p).stat().st_mtime_ns > scan_finished]
    assert not changed_during_refresh, ('sources changed during refresh; re-extract before stamping', changed_during_refresh)
    extraction = read('.graphify_extract.json')
    old = read('graph.before-refresh-20260922.json')
    graph = build_from_json(extraction, root=ROOT, directed=old.get('directed', False))
    analysis = read('.graphify_analysis.json')
    communities = {int(k): v for k, v in analysis['communities'].items()}
    cohesion = {int(k): v for k, v in analysis['cohesion'].items()}
    labels = {int(k): v for k, v in read('.graphify_labels.json').items()}
    assert set(labels) == set(communities), 'every community requires a reviewed label'
    assert all(not v.startswith('Community ') for v in labels.values())
    required = {
        'WorkAdmission': 'services/work_admission.py',
        'WorkContracts': 'services/work_contract.py',
        'WorkScheduler': 'services/work_scheduler.py',
        'WorkReservations': 'services/work_reservations.py',
        'DelegationSnapshots': 'services/delegation_snapshot.py',
        'KnowledgePolicy': 'services/knowledge_policy.py',
        'KnowledgeRevisions': 'services/knowledge_revisions.py',
        'WorkRepository': 'clients/work_repository.py',
    }
    checked = {}
    for label, suffix in required.items():
        matches = [(n, d) for n, d in graph.nodes(data=True) if d.get('label') == label and str(d.get('source_file', '')).endswith(suffix)]
        assert matches, ('missing required new source symbol', label)
        for nid, data in matches:
            path = ROOT / data['source_file']
            text = path.read_text()
            assert f'class {label}' in text, (label, path)
            assert graph.degree(nid) > 0, ('isolated required class', label)
        checked[label] = [{'id': n, 'file': d['source_file'], 'location': d.get('source_location'), 'degree': graph.degree(n)} for n, d in matches]
    write('refresh-source-verification.json', checked)
    questions = suggest_questions(graph, communities, labels, top_n=3)
    if not to_json(graph, communities, str(OUT / 'graph.json'), community_labels=labels):
        raise RuntimeError('Graph shrink guard refused publication; old graph preserved')
    report = generate(graph, communities, cohesion, labels, analysis['gods'], analysis['surprises'], detection, {'input': 0, 'output': 0}, str(ROOT), suggested_questions=questions)
    report = report.replace('- Token cost: 0 input · 0 output', '- Token cost: unavailable for host-agent semantic extraction; AST uses no LLM. Zero placeholders are not measured usage.')
    report += '\n## Refresh limits and verification\n\n'
    report += '- All detected code, documents and images were processed; four sensitive-name exclusions remain as configured.\n'
    report += '- Two videos could not be transcribed because faster-whisper is unavailable; they remain pending, not stamped complete.\n'
    report += '- Existing undirected graph mode preserved. Raw extraction diagnostics are in refresh-health.json; repeated endpoints may collapse relationships.\n'
    report += '- Generated knowledge is not proof that planned roadmap behavior is implemented. Important conclusions require source verification.\n'
    report += '- No application code changed in this refresh. No Obsidian copy: this is reproducible generated knowledge.\n'
    report += '\nSensitive-name exclusions (detector policy; not a finding of exposed secrets):\n\n'
    for path in detection.get('skipped_sensitive', []):
        report += f'- `{Path(path).relative_to(ROOT)}`\n'
    health = read('refresh-health.json')
    report += '\nRaw extraction diagnostic counts:\n\n'
    for key in ('dangling_endpoint_edges', 'missing_endpoint_edges', 'self_loop_edges', 'directed_same_endpoint_collapsed_edges', 'undirected_same_endpoint_collapsed_edges'):
        report += f'- `{key}`: {health.get(key, 0)}\n'
    (OUT / 'GRAPH_REPORT.md').write_text(report)
    fresh = read('.graphify_fresh.json')
    semantic = read('.graphify_semantic.json')
    uncached = (OUT / '.graphify_uncached.txt').read_text().splitlines()
    saved = save_semantic_cache(semantic['nodes'], semantic['edges'], semantic['hyperedges'], root=ROOT, allowed_source_files=uncached, prompt_file=SPEC)
    finalize(saved)


def finalize(saved=None):
    from graphify.cache import check_semantic_cache
    from graphify.detect import save_manifest
    from graphify.cli import _stamped_manifest_files
    detection = read('.graphify_detect.json')
    semantic = read('.graphify_semantic.json')
    old = read('graph.before-refresh-20260922.json')
    current = read('graph.json')
    files = [p for kind in ('document', 'paper', 'image') for p in detection['files'][kind]]
    if saved is None:
        _, _, _, missing = check_semantic_cache(files, root=ROOT, prompt_file=SPEC)
        assert not missing, ('semantic cache incomplete', missing)
        saved = len(files)
    # Resolve each distinct source once, rather than once per graph node.
    source_names = {n['source_file'] for n in read('.graphify_ast.json')['nodes'] if n.get('source_file')}
    ast_sources = {str((ROOT / source).resolve()) for source in source_names}
    failed_ast = (set(detection['files']['code']) - ast_sources) | {str((ROOT / p).resolve()) for p in read('.graphify_ast.json').get('failed_sources', [])}
    semantic_sources = {n['source_file'] for n in semantic['nodes']} | {h['source_file'] for h in semantic['hyperedges']}
    stamp_evidence = {'nodes': [{'source_file': source} for source in semantic_sources]}
    stamp = _stamped_manifest_files(detection['files'], stamp_evidence, ROOT, failed_ast_sources=failed_ast)
    # Videos failed transcription: do not stamp them by accident.
    stamp['video'] = []
    save_manifest(stamp, root=ROOT, scan_corpus={p for fs in detection['files'].values() for p in fs}, clear_ast=failed_ast or None)
    (OUT / '.graphify_root').write_text(str(ROOT))
    info = dict(date=datetime.now(timezone.utc).isoformat(), old_nodes=len(old['nodes']), new_nodes=len(current['nodes']), new_edges=len(current['links']), semantic_cached_files=saved, ast_without_nodes=sorted(failed_ast), input_tokens=None, output_tokens=None, token_usage_status='not exposed by host agent tools', pending_videos=detection['files']['video'])
    write('refresh-summary.json', info)
    cost_path = OUT / 'cost.json'
    cost = json.loads(cost_path.read_text()) if cost_path.exists() else {'runs': []}
    cost.setdefault('runs', []).append(info)
    cost['total_input_tokens'] = None
    cost['total_output_tokens'] = None
    cost['token_usage_status'] = 'unknown: host-agent usage unavailable; no estimated savings'
    write('cost.json', cost)
    print('PUBLISHED', json.dumps(info), flush=True)


if __name__ == '__main__':
    {'validate': validate, 'build': build, 'publish': publish, 'finalize': finalize}[sys.argv[1]]()
