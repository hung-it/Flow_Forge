# Workflow, draft and publication API

Implemented under `/api/v1`; requires a live authenticated session. OWNER and DEVELOPER can edit; VIEWER can read. Project/member administration remains OWNER-only. Non-members receive 404.

## HTTP contract

- POST/GET `/projects/{project_id}/workflows`: create (`{name}`) or list. The optional `q` list filter matches a case-insensitive literal substring of the workflow name, after trimming surrounding spaces. It accepts up to 100 characters.
- GET/PATCH `/workflows/{workflow_id}`: detail or rename (`{name}`). Names are trimmed, 1?100 characters; duplicate names allowed.
- GET `/workflows/{workflow_id}/draft`: current `{workflow_id,dag,revision,updated_by,updated_at}`.
- PUT `/workflows/{workflow_id}/draft`: `{expected_revision,dag}` replaces the entire JSON object and increments revision, including identical saves. Stale revision returns 409 `DRAFT_REVISION_CONFLICT`.
- POST `/workflows/{workflow_id}/versions`: `{draft_revision}` validates and publishes the stored draft; returns 201 with immutable snapshot. Does not modify draft/revision. Repeated requests create additional versions: publication is not idempotent. After an uncertain network outcome, inspect version history before retrying.
- GET `/workflows/{workflow_id}/versions`: metadata only, newest first.
- GET `/workflows/{workflow_id}/versions/{version}`: full published snapshot, explicit positive version number; no latest alias.

Lists use limit (default 20, max 100) and offset (default 0). Workflow ordering is created_at then UUID. New workflows atomically receive a revision-1 draft with empty nodes/edges. Drafts can store incomplete/invalid DAGs; publication must satisfy WorkflowSpec and exact required handler binding names. Rename changes metadata only; DAG name remains a snapshot content label.

Request bodies must be application/json and at most 256 KiB in total, including streamed requests. JSON roots must match request schemas; DAG must be an object. Non-finite numbers (including exponent overflow), NUL and invalid Unicode surrogate strings are rejected before PostgreSQL JSONB writes. Existing 8 KiB auth limit is preserved. Errors use the existing code/message/details/request_id envelope: 401 invalid session, 403 insufficient role, 404 invisible/missing resource, 409 stale revision, 413 oversized request, 415 media type, 422 invalid input/DAG, 503 database unavailable.

## Snapshot and concurrency

Publication expands defaults, sorts nodes by key and edges lexically, and hashes UTF-8 compact sorted-key JSON containing the normalized DAG and used handler-contract schemas. UUIDs, timestamps and publication numbers are excluded. Identical effective content has the same hash but is not deduplicated. Handler contract version and input/output schemas are stored with each version. Contract changes cannot silently rewrite published history.

Writes lock session, project, then workflow; authorization is re-read after the project lock. Draft revision comparison, validation, number allocation and insertion happen within the transaction. A failed publication consumes no version. PostgreSQL unique constraints prevent duplicate numbers and a trigger rejects UPDATE/DELETE of published versions. There is no delete/archive API. Downgrade drops the workflow tables and must only be exercised on disposable test databases.

A draft that fails publication validation returns `422 WORKFLOW_INVALID`. Its `details` entries
contain `loc` (the path in the draft prefixed by `dag`), `type` and a user-facing `message`.

## Trusted handler contracts v1

All payload schemas are strict and reject extra fields. Binding names must match the input model fields exactly. JSON Pointer syntax and ancestry are checked at publication; resolved values/types are validated when handlers execute in the later worker milestone.

- Document: title (1?200 characters), content (1?12000); both nonblank.
- Metadata: title (1?200 characters), character_count (nonnegative integer).
- Analysis: word_count and line_count (nonnegative integers).
- Summary: metadata plus analysis.
- validate_document: `{document: Document}` -> Document.
- extract_metadata: `{document: Document}` -> Metadata.
- analyze_content: `{document: Document}` -> Analysis.
- aggregate: Summary -> Summary.
- generate_report: `{summary: Summary}` -> `{summary: Summary, report_text: string}`.

The [Run API](runs.md) persists runs from published versions and validates run-input pointers. The dashboard can inspect a published DAG and start a run from the selected version.

## Verification

PostgreSQL integration tests cover publication history, DB immutability, RBAC, stale revision, concurrent saves/publications, save-versus-publish, and streamed UTF-8 limits. Real HTTP acceptance exercises auth, project access, draft validation, repeated publication, VIEWER restrictions and old-version preservation. Smoke additionally performs restart/outage and migration drills in an isolated Compose project.

Coverage explicitly supports SQLAlchemy greenlet switching (`concurrency = ["thread", "greenlet"]`); each domain retains its >=80% gate. This avoids missing async continuation lines, as required by https://coverage.readthedocs.io/en/latest/config.html#run-concurrency.
