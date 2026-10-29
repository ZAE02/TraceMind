CREATE TABLE IF NOT EXISTS tracemind_memory_revisions (
    subject_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    id text NOT NULL UNIQUE,
    snapshot jsonb NOT NULL,
    previous_hash text,
    snapshot_hash text NOT NULL,
    PRIMARY KEY (subject_id, revision)
);
