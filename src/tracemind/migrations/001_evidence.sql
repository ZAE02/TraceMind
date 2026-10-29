CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS evidence (
    id text PRIMARY KEY,
    case_id text NOT NULL,
    session integer NOT NULL CHECK (session >= 0),
    turn integer NOT NULL CHECK (turn >= 0),
    role text NOT NULL,
    text text NOT NULL,
    occurred_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1024) NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_case_cutoff_idx ON evidence (case_id, session, turn);
CREATE INDEX IF NOT EXISTS evidence_embedding_idx ON evidence USING hnsw (embedding vector_cosine_ops);

