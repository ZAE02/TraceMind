# Scoped v2 dispatch

`scripts/dispatch_scoped.py` is a file bridge for manually created Codex agents. It does not spawn agents or call a model API. It uses `.local/datasets/v2` and the PostgreSQL job queue. Start it only after the v2 builder with case selection is available and the control file is ready:

```powershell
.venv/Scripts/python.exe scripts/dispatch_scoped.py
```

Create `.local/scoped/control.json` with explicit enabled groups and cases, plus each case's dedicated client and counselor task identity. Add each shared anchor job ID and its own freshly created task identity to `anchor_registry` before importing that anchor's result. All task identities must be unique across case roles and anchor jobs. For example:

```json
{
  "enabled_groups": ["G001"],
  "enabled_cases": ["v2_G001-T1"],
  "worker_registry": {
    "v2_G001-T1": {
      "client": "/root/client_v2_G001_T1",
      "counselor": "/root/counselor_v2_G001_T1"
    }
  },
  "anchor_registry": {}
}
```

The dispatcher exports pending dialogue jobs only to `.local/scoped/<case>/<role>/<job-id>.json`; shared anchors go only to `.local/scoped/anchors/<job-id>.json`. Exports require the v2 namespace, matching scope, purpose, case, session and role; enabled case roles must be registered. An existing file is never rewritten or deleted merely because its job became stale. Treat files left in these folders as historical: always check that the job remains pending and enabled before assigning work.

Write an agent's completed response atomically to `.local/scoped/results/<job-id>.json` with the envelope `{ "job_id": "...", "result": {...}, "provenance": {"model": "gpt-6.1-terra", "reasoning": "medium", "agent_task": "/root/..."} }`. The dispatcher checks the job, enabled scope, registered identity and exported destination, then calls `JobQueue.complete`, which validates the schema and binds the task to its scope and role. Accepted files move to `imported`; the dispatcher stops on a malformed or unauthorized result so it can be investigated. Agent-provided provenance is a reported identity, not cryptographic proof.

A Windows lock prevents a second dispatcher. Create `.local/scoped/STOP` to pause imports, scheduling and exports; remove it to resume. Results are scanned every second. Builder advancement runs immediately after an import and at most once every ten seconds while idle. Control changes are reloaded during operation. Keep credentials and private plans out of control and result metadata.
