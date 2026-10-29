# TraceMind synthetic dataset data card

Status: **GENERATION IN PROGRESS**. Twenty individualized scene plans have passed independent, digest-bound model review. Actual dialogue and experiment completion counts are reported separately in `status.json`. No human ratings have been collected. The characters and circumstances are fictional adults, not clinical records. The plans do not establish diagnoses or require positive outcomes.

The intended design contains 20 groups, four in each of five themes. Within each theme, two groups are development, one calibration, and one test. Each group has four alternative trajectories (stable, gradual, fluctuating, contextual), six planned sessions per trajectory, and three shared checkpoint prompts at sessions 2, 4, and 6. Checkpoint text is designed to fit all four alternative histories; it does not by itself resolve the historical interpretation. The plans are prompts for later authoring, not completed dialogues or validated evidence of quality.

Early scaffold drafts were replaced by individually authored session experiences and disclosure constraints before approval. The reviewer rejected and required repairs for several factual and counterfactual inconsistencies; duplicate-string checks alone did not constitute quality approval. No experiment scores or human judgments are supplied.

At S2/S4/S6, the shared current fragment is the opening client utterance, generated once from the common situation and reused across four histories with its source job retained. Each remaining utterance is separately authored by an isolated client or counselor role. The counselor receives public dialogue only, and the client receives only events experienced by that session. Reused anchors are not represented as four independent generations.

Private plans, reviews and held-out data remain under ignored `.local/datasets/v1/`. Agent tasks are explicitly routed to `gpt-6.1-terra` with `medium` reasoning; the job log records role, task identity and input/result hashes. Platform-unavailable token usage and server-returned model snapshots are not invented. Provenance is not cryptographic proof of execution.
