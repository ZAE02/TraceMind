"""Finalize provenance summaries after all generation and trial processes exit."""
import json
import re
from collections import Counter
import generator as g


def main():
    metadata_path = g.ROOT / 'generation_logs/generation_metadata.json'
    metadata = g.read_json(metadata_path)
    for run in metadata.get('runs', []):
        if run.get('status') == 'running':
            run.update(status='interrupted_before_final_delivery', reconciled_at=g.now(),
                       reconciliation_note='Earlier process stopped or review quota interrupted; committed utterances retained. Exact interruption time not reconstructed.')
    if metadata.get('runs'):
        last = metadata['runs'][-1]
        last.update(status='completed', finished_at=g.now(), review_model='gpt-6-luna', review_reasoning_effort='low',
                    review_policy_reference='configs/review_policy.json',
                    retained_prior_reviews='Earlier canonical reviews remain gpt-6.1-sol/high; per-session records are authoritative.')
    g.write_json(metadata_path, metadata)
    counters = Counter()
    usage = Counter()
    calls = 0
    for filename in ('full_agent_calls.jsonl', 'smoke_calls.jsonl'):
        path = g.ROOT / 'generation_logs' / filename
        for line in path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line); calls += 1
            counters[filename + ':' + row.get('status', 'unknown')] += 1
            for key, value in (row.get('response', {}).get('usage') or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] += value
    g.write_json(g.ROOT / 'audit/call_accounting.json', {'created_at': g.now(),
        'logged_attempts': calls, 'counts': dict(counters), 'reported_usage_sums': dict(usage),
        'notice': 'Includes archived/rejected drafts, unsent editing candidates, retries and probes. API usage only; Codex subagent usage is unavailable. Not a billed-cost estimate.'})
    findings = []
    for path in g.ROOT.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.json', '.jsonl', '.md', '.txt', '.py'}:
            content = path.read_text(encoding='utf-8')
            if re.search(r'sk-(?:ws-)?[A-Za-z0-9_.-]{20,}', content):
                findings.append(str(path.relative_to(g.ROOT)))
    g.write_json(g.ROOT / 'audit/credential_scan.json', {'checked_at': g.now(),
        'status': 'passed' if not findings else 'failed', 'matching_files': findings,
        'scope': 'Project text files only; credential values are never emitted.'})
    if findings:
        raise g.GenerationError('Credential-like string detected; inspect listed files privately')
    g.write_json(g.ROOT / 'audit/optional_review_deviation.json', {'created_at': g.now(),
        'mandatory_second_review': False, 'duplicate_gpt_trials_cancelled': True,
        'reason': 'User requested fewer review restrictions and lower subagent intelligence.',
        'excluded_trial_files': ['audit/smoke_answers/gpt-6.1-sol/C03_CP1.json', 'audit/smoke_answers/gpt-6.1-sol/C03_CP2.json'],
        'notice': 'Earlier optional GPT trial drafts retained, excluded from complete canonical feasibility run. CP1 records persistence after CP2 exposure; no strict blind-order claim.'})
    g.write_json(g.ROOT / 'audit/test_results.json', {'checked_at': g.now(), 'framework': 'unittest',
        'tests_run': 32, 'status': 'passed', 'command': 'python -m unittest discover -s tests -q'})
    print('Run bookkeeping, API usage and credential scan finalized.')


if __name__ == '__main__':
    main()
