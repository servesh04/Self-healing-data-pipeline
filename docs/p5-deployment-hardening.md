# P5 external orders deployment boundary

P5 keeps the P4 batch and mapping semantics. It separates three bearer
credentials in `AUTH_MODE=production`: `SHARED_TOKEN` for the existing browser
dashboard, `LOADER_SERVICE_TOKEN` for trigger/release and the minimal
feed/batch status endpoint, and `APPROVER_TOKEN` for human external-batch
approval/rejection. The three must be distinct and at least 24 characters.
The loader process receives only `P5_LOADER_SERVICE_TOKEN`; the independent
approver process receives only `P5_APPROVER_TOKEN`. Set `AUTH_MODE=production`
in each process so the CLI never falls back to `P4_AGENT_TOKEN`. Do not bundle
either private credential in the frontend. Demo mode remains the default for
local compatibility and accepts `SHARED_TOKEN`/`P4_AGENT_TOKEN` as before.

The loader polls `GET /api/external/orders/{feed}/{batch}/status`, which
returns only run ID and status. It cannot call the full run detail endpoint.
The approver uses `POST .../{feed}/{batch}/approve` or `/reject`; the server
binds the batch to its recorded run. Production browser credentials cannot
read external run detail/history, use external run chat, select another
pipeline environment in analytics, label external proposal components, or
decide an external run by run ID.
The approver credential cannot trigger/release a batch or decide a dashboard
run. The loader credential cannot approve/reject.

## Deployment sequence

1. In a dedicated database or isolated test schema, run
   `scripts/migrate_p5.py` with `P5_MIGRATION_DATABASE_URL` set to a
   schema-owner role. The production API does **not** run
   DDL; it checks required tables at startup and refuses to start if absent.
   `/api/ping` also checks schema without writing a probe in this mode.
2. Create distinct login roles `p5_api` and `p5_loader` with passwords.
   Review `scripts/p5_roles.sql` against the target schema and apply it as
   owner. It grants API mapping/checkpoint writes and release authorization,
   while only the loader can stage and publish. Neither role gets schema
   CREATE. Review inherited role memberships and `PUBLIC` grants: the script
   grants only explicit rights and does not revoke privileges inherited from
   unrelated roles. Keep migration-owner credentials out of both processes.
3. Run `scripts/verify_p5_roles.py` with `P5_API_DATABASE_URL` and
   `P4_LOADER_DATABASE_URL` against that isolated database. It reads Postgres
   privilege metadata and fails if the loader can insert releases, mappings
   or approval decisions, or if the API can publish rows. It makes no writes.
4. Set API `DATABASE_URL` to the `p5_api` role, set all three API tokens,
   and set `AUTH_MODE=production`. Run the loader with its DB URL and service
   token. Run human decisions from a separate process with approver token;
   it needs no database URL.
5. Repeat the batch gate with `AUTH_MODE=production`,
   `P5_LOADER_SERVICE_TOKEN`, `P5_APPROVER_TOKEN`, `SHARED_TOKEN`, the loader
   database URL and a read-only `P5_AUDIT_DATABASE_URL` that can select
   `mapping_current`. Run `scripts/verify_p4_orders.py --expected-env p5-unique-scope`.
   This tests real wrong-role HTTP responses, initial failure, approval,
   exact release, destination publish and retry. Do not apply the role script to the current
   live database without reviewing current grants and dependencies.

The loader's publish transaction uses a transaction advisory lock to
serialize retries, then reads immutable stage/release/mapping rows. It does
not need UPDATE on those tables. Postgres transaction rollback protects the
destination batch when validation or insertion fails. Authorization remains
in the API database role; possession of only the loader DB role and loader
HTTP token cannot forge a release or an approval event.

This is a narrow credential and SQL-role boundary, not a complete identity
system. Human approvers share one configured credential; named user
attribution, key rotation, audit export, and network segregation require a
separate deployment design.

## Verification on 25 October 2026

The 184-test local suite, frontend build, and Python compilation passed. An
isolated live API/Postgres/LLM gate ran with `AUTH_MODE=production` in
`p5-20261025-auth`. Wrong-role trigger, release, status, and approval calls
were denied. A clean control published 50 rows at mapping version 277; a
human-approved rename published 50 rows at version 279; an exact retry
inserted no duplicates; the unsafe case published zero rows. The other feed's
mapping pointer stayed at version 277. Test batch and destination rows remain
for audit. This first gate used the existing database owner connection.

A subsequent isolated gate created a temporary database, migrated it as owner,
applied `scripts/p5_roles.sql`, and passed `scripts/verify_p5_roles.py` under
separate `p5_api` and `p5_loader` logins. The live API/LLM batch gate then ran
with those restricted roles: 50 clean rows, 50 repaired rows, no duplicate
rows on retry, and zero published rows for the unsafe case. Wrong-role HTTP
calls were denied. The temporary database and both test roles were removed.
The first isolated attempt was interrupted by a transient database DNS
failure; a fresh rerun passed. The reusable gate is
`python scripts/verify_p5_isolated.py`; it requires an owner `DATABASE_URL`
with CREATEDB and CREATEROLE, `SHARED_TOKEN`, and `GROQ_API_KEY`, and refuses
to reuse pre-existing `p5_api` or `p5_loader` roles. Production deployment
and network boundaries remain separate decisions.
