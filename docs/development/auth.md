# Auth and Projects API

Implemented for local MVP: bootstrap user, JWT login/me, rotating refresh cookie, session-scoped logout,
project metadata, membership and ownership transfer. No login UI, public registration, password reset,
OAuth provider, MFA, invitations, account deletion or project deletion in this milestone.

## Start and create users

From the repository root run `./scripts/init-env.ps1` once, then `docker compose up -d --build --wait`.
The initializer creates .env with a random signing key and refuses to replace an existing file.
An existing .env must have FLOWFORGE_JWT_SECRET set to at least 32 random bytes, kept private and stable
across restarts. No signing key is committed; smoke generates its own ephemeral key.

Run `docker compose exec api python -m flowforge.bootstrap --email owner@example.com`.
The command prompts for password/confirmation without echo. Repeat with another email to create members.
Passwords require 12–128 characters and are not trimmed. Duplicate canonical email fails without changing
the existing password. Email is stripped/lowercased and validated consistently in bootstrap/login/member lookup.
No password argument, default account or automatic startup seeding is provided.

## Try the API in Swagger

Open http://localhost:8000/docs. On POST /api/v1/auth/login set **X-FlowForge-CSRF** to **1**,
then submit JSON email/password. The response contains access_token, token_type=bearer and expires_in=1800.
The browser stores the refresh cookie. Use Swagger's Authorize button with the access token for protected APIs.
POST refresh/logout use the same CSRF header and body `{}`. GET me reads the current principal.
The frontend currently only shows infrastructure health; login screens are a later milestone.

## Session contract

- Access JWT: HS256 only, required sub/sid/iat/exp/iss/aud, 30-minute lifetime. No role claims.
  Each protected request checks a live DB session/user; session expiry/revocation can end access sooner.
- Each login starts an independent session with absolute expiry 7 days later. Rotation never extends it.
- Refresh: opaque 256-bit random token; DB stores only SHA-256 hashes. Token history detects reuse.
  History is retained at least through expiry; automatic cleanup of session history is not yet provided.
- Cookie: flowforge_refresh; HttpOnly, SameSite=Lax, host-only, path /api/v1/auth.
  FLOWFORGE_COOKIE_SECURE=false is for local HTTP; set true when serving HTTPS.
- Login/refresh/logout require application/json and X-FlowForge-CSRF: 1. Browser Origin, when supplied,
  must be in FLOWFORGE_ALLOWED_ORIGINS (JSON list); default localhost/127.0.0.1 ports 8000/5173.
  CLI clients may omit Origin but must send the CSRF header. No cross-origin credentials CORS is enabled.
  If API_PORT/WEB_PORT changes, update FLOWFORGE_ALLOWED_ORIGINS in .env to the actual browser origins.
- Rotation is atomic under the session lock. A known consumed token revokes that session and returns 401.
  A random unknown token returns 401 without revoking an unrelated session.
- Strict race policy: concurrent refreshes of one token can return 200 + 401 and revoke even the winner's
  newly issued credentials. Future clients must coordinate a single refresh across requests/tabs.
- Logout uses the refresh cookie, including when access JWT has expired. Repeated logout returns 204.
  It revokes only the current session. Requests already authenticated/in flight may complete;
  requests checking the session after logout commit fail. Mutations recheck/lock the session in transaction.
- All auth responses use Cache-Control: no-store. Errors/logs never include password/token/hash inputs.
- Login throttling: 10 attempts per UTC minute/IP, successful or failed; counters are atomic in PostgreSQL,
  shared by API instances. 429 includes conservative Retry-After: 60. Old buckets are removed on login.
  Raw connection IP is used; uvicorn proxy-header trust is disabled. A dev proxy can share one IP/budget.
  Hash/verify operations are limited to two concurrent jobs per process to bound Argon2 memory use.

## Projects and permissions

Any authenticated user may POST /api/v1/projects; project and matching OWNER membership commit together.
GET /projects lists only joined projects; GET /projects/{id} and /members allow all members.
Project/member lists use limit=20, max 100, offset=0. Names are nonblank, max 100 characters.

Only OWNER can PATCH project name, POST members by existing email, PATCH member role or DELETE a member.
The allowed assigned roles are DEVELOPER/VIEWER. An OWNER cannot be deleted/demoted through these APIs.
POST /projects/{id}/transfer-ownership accepts user_id of an existing member; the former owner becomes
DEVELOPER. The current owner as target is 409; a nonmember target is 404. Exactly one OWNER is retained.
All mutations lock session then project, and read membership after the project lock is acquired;
stale requests cannot use a role read before waiting for another mutation.

HTTP semantics: 401 invalid/missing authentication; 403 insufficient role in visible project or CSRF;
404 absent/invisible project or missing target; 409 duplicate membership/ownership conflict;
422 validation; 413 auth body over 8 KiB; 415 wrong auth content type; 429 login rate; 503 DB unavailable.
Errors contain code/message/details/request_id, with X-Request-ID in the response; credential inputs are omitted.
Removal/role changes affect future requests even with an older valid JWT because roles are loaded from DB.

## Database and verification

0003_auth_sessions adds auth_sessions, refresh_tokens and login_counters without editing old migrations.
Existing user/project/member rows remain unchanged. Downgrade 0003 removes only the new auth state and
invalidates sessions; it does not remove identity data. Existing owner consistency drift is not auto-repaired.

Run check.ps1 for unit/static/audit gates and smoke.ps1 for real PostgreSQL tests, coverage by domain,
real HTTP acceptance, outage recovery and upgrade/downgrade on disposable volumes.
Auth, projects and contracts each require >=80% coverage on the combined suite, not just contract coverage.
No test uses the development database automatically; the smoke harness creates its own project/credentials.
Auth APIs require DB availability; a DB outage returns 503, not an authentication bypass or password failure.
