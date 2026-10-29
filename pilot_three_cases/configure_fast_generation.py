"""User-authorized low-effort continuation; exact saved prefix remains unchanged."""
import shutil
import subprocess
import generator as g

root = g.ROOT
lock = root / 'generation_logs/runner.lock'
if lock.exists():
    pid = g.read_json(lock)['pid']
    check = subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-Process -Id %d -ErrorAction SilentlyContinue' % pid], capture_output=True)
    if check.stdout.strip():
        raise g.GenerationError('Generator still alive; wait for exit')
    g.write_json(root / 'generation_logs/interrupted_attempts/fast_generation_stop.json', g.read_json(lock))
    lock.unlink()
backup = root / 'configs/revisions/user-authorized-fast-v9'
if backup.exists():
    raise g.GenerationError('Immutable configuration snapshot already exists')
backup.mkdir(parents=True)
shutil.copyfile(str(root / 'configs/generation_config.json'), str(backup / 'generation_config.json'))
config = g.read_json(root / 'configs/generation_config.json')
config.update(prompt_version='pilot-v9-user-authorized-low-effort',
              role_reasoning_effort={'client': 'low', 'counselor': 'low'}, expression_gate=False)
g.write_json(root / 'configs/generation_config.json', config)
entries = []
prompts = {role: (root / 'configs' / (role + '_system_prompt.txt')).read_text(encoding='utf-8') for role in g.ROLES}
for path in (root / 'dialogues/C02').glob('S*.json'):
    data = g.read_json(path)
    if data['status'] in g.COMPLETE_STATUSES:
        continue
    number = data['session_number']
    archive = root / 'revisions/C02' / ('S%02d' % number) / 'before_fast_continuation.json'
    if archive.exists():
        raise g.GenerationError('Immutable saved prefix snapshot exists')
    g.write_json(archive, data)
    plan = g.read_json(root / 'configs/case_plans/C02.json')
    previous = g.public_history(root, 'C02', number)
    ledger = g.read_json(root / 'facts/C02.json')
    fingerprint = g.digest({'generation': config, 'case': plan, 'prompts': prompts, 'previous': previous,
                           'confirmed_additions': [f for f in ledger.get('confirmed_additions', []) if f['first_session'] < number]})
    data.update(config_hash=fingerprint, generation_config_snapshot=config, prompt_version=config['prompt_version'],
                preserved_prefix_length=len(data['utterances']), continuation_changed_at=g.now(),
                continuation_note='User requested lower intelligence/review constraints and reduced time/token use. Exact prior utterances retained; subsequent calls low, no expression editing gate.')
    g.write_json(path, data)
    entries.append({'session': number, 'preserved_utterances': len(data['utterances']), 'prior_snapshot': str(archive.relative_to(root))})
revisions = g.read_json(root / 'audit/configuration_revisions.json')
revisions['revisions'].append({'timestamp': g.now(), 'version': config['prompt_version'],
    'reason': 'User explicitly requested lower subagent intelligence, fewer review restrictions, less time/tokens.',
    'role_overrides': config['role_reasoning_effort'], 'expression_gate': False,
    'original_configs': str(backup.relative_to(root)), 'psychological_gold_added': False, 'entries': entries})
g.write_json(root / 'audit/configuration_revisions.json', revisions)
print('Low-effort continuation configured; exact committed prefix preserved.')
