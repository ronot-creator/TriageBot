# Project Overview & Architecture

> Snapshot updated: 2026-10-18. Repository: ronot-creator/TriageBot. PR branch: fix/audit-announcement-fallback-help (see PR #11 for the live head SHA), not production. PR #11 is open and unmerged. The latest executable-code/test changes were made at 9e6a4e66cbd96ff110e4d4bdf7c5a8cfe285f2df; later commits refresh documentation and handover notes. Production was last verified on main at b866af600cce323521faec78dbb53219aed633a7. Do not merge or deploy without explicit owner approval.

## Purpose

**SVU Med Triage Bot** is a bilingual (Arabic/English) Telegram assistant for first-year medical students. It monitors configured group conversations with local keyword and intent checks before spending a Gemini request; triages relevant messages into urgency-ranked tickets for human leaders; maintains FAQs, blacklist, configuration and usage records in Turso/libSQL; answers private questions using source-grounded college data or clearly labelled general knowledge; imports and answers timetable/exam questions deterministically; accepts authorized leader activity updates; and ingests official-channel posts as drafts requiring human approval before publication. The bot is not an authority: leaders retain final decisions, and unverified student claims must never become official timetable facts.

## Runtime stack and architecture

- **Python:** CI uses Python 3.12. Keep CI green and do not assume a different production runtime without checking Render.
- **Telegram:** python-telegram-bot >=22.0,<23.0. Runtime uses Application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False). This is long polling, not a custom webhook.
- **AI:** Google Gen AI SDK google-genai >=1.0.0; Pydantic structured response models.
- **Strict model allowlist:** only gemini-3.5-flash-lite (primary) and gemini-3.1-flash-lite (fallback). No OpenRouter, Gemma, automatic router or unapproved model. Defaults: 15 requests/minute and maximum 500 requests/day per model, clamped by code. Provider-side quota availability is independent.
- **Persistence:** Turso/libSQL using libsql-client >=0.3.1. A libsql:// URL is converted to HTTPS transport. Turso is the durable production source; Render local disk is not durable.
- **Other dependencies:** pydantic >=2.0,<3.0, openpyxl >=3.1,<4.0, plus requests and openai retained at the owner's explicit preference. Active usage of requests/openai was not confirmed in this snapshot; audit before proposing removal.
- **Workbook import:** assets/Medical_Timetable_FirstYear_Complete.xlsx; loader uses openpyxl. bot.py imports it at startup if present, before Telegram polling.
- **Health server:** ThreadingHTTPServer listens on 0.0.0.0:$PORT (default 10000), on a daemon thread. GET / and GET /health/live return 200. GET /health/ready returns 200 only after DB initialization and Telegram get_me(), otherwise 503. Unknown paths return 404.
- **Deployment:** known Render URL https://triagebot-wrzu.onrender.com. Intended build command: pip install -r requirements.txt; intended start command: python bot.py. Confirm actual dashboard settings before changing them. UptimeRobot was configured to poll /health/ready every five minutes. A Turso token update was saved in Render but reported as not deployed at the time; verify without revealing secret values.
- **Authority rule:** Python controls permissions, quotas, schema validation, deduplication and writes. Gemini can interpret text but cannot authorize a sender, generate SQL, or convert a student's rumor into an official update.

## Main workflows

1. **Group messages:** local detection checks mention, reply-to-bot, active keyword and schedule-question intent. Untriggered chatter should not call Gemini. A triggered message is deduplicated by (chat_id,message_id), optionally downloads supported media within the configured size limit, builds bounded context and invokes an approved model. Structured triage can produce an FAQ reply or leader ticket; AI failures must fail safely.
2. **Private Q&A:** ordinary private text is answered directly. Timetable/TBL queries use deterministic timetable logic first. Other questions use read-only evidence from active FAQs, announcements, activity updates, imported timetable rows and exam records. Database claims require source identifiers validated against actual rows. Missing/conflicting evidence must be stated. General-knowledge answers must be labelled general, not official college information. Private history must not mix users. /morehelp explicitly forwards the next private message to the owner.
3. **Leader activity updates:** in the configured leader group, a local pre-filter looks for an activity plus status/date/time cue. Only a candidate from an authorized leader goes to the existing structured /addinfo parser. Clear validated changes are saved in activity_changes; ambiguous changes are stored in pending_clarifications and require a clarification. timetable_qa.py overlays saved changes when answering schedule questions.
4. **Generic timetable upload:** leader uploads CSV/XLSX/XLSM; code parses and previews rows, then a leader explicitly approves add/replace/cancel. Manual changes are audit logged.
5. **Workbook timetable:** separate importer loads timetable_events, exam_events, translation_aliases and interpretation_rules. Timetable answers use deterministic code and parameterized SQL; the LLM must not invent schedule facts.
6. **Official announcements:** posts from a configured channel are classified and saved with source text/link/date. Relevant items remain drafts for leader review; only explicit approval publishes to the discussion group.

## Telegram API and payload contract

- Library-managed long polling calls Telegram Bot API getUpdates; no custom webhook receiver exists. Telegram's standard JSON Update schema is mapped to telegram.Update and telegram.Message. All update types are requested.
- Handlers cover messages, text/captions, supported media, timetable documents, channel posts and inline callback queries. No custom Telegram payload schema exists.
- Bot identity: @svu_med_triage_bot. Required secret: TELEGRAM_BOT_TOKEN. Never print the token, put it in source/docs, or log Telegram API URLs.
- Student group visibility depends on BotFather privacy mode and group permissions; privacy mode can prevent the bot from receiving arbitrary group messages unless disabled or the bot is admin.
- Leader group must be configured by the owner running /setleaders inside the intended group. It is not configured yet according to the last owner-confirmed status. Do not configure it automatically.
- Official news channel: bot must be administrator; set numeric NEWS_CHANNEL_ID. Publication target is numeric DISCUSSION_GROUP_ID. All eligible groups are monitored. The configured leaders' discussion is observed and persisted but excluded from student triage to avoid creating tickets in the internal leaders' chat.
- Identity comes from numeric Telegram user ID and get_chat_member status, never username or self-asserted title. Owner ID is OWNER_TELEGRAM_ID; optional trusted numeric leaders are in LEADER_TELEGRAM_IDS. The owner must open the bot and press Start once before private owner escalation can be delivered.
- Implemented commands: /start, /help, /morehelp, /chatid, /setleaders, /status, /setterm, /date, /addinfo, /addword, /delword, /listwords, /addfaq, /listfaqs, /delfaq, /archivefaq, /reviewold, /timetable, /timetablechange, /pendingannouncements, /approveannouncement, /editannouncement, /rejectannouncement.
- Callback families: timetable import actions timetableapprove|timetablereplace|timetablecancel:<id>; ticket actions claim|resolve|blacklist:<id>.

# Current State & Existing Files

## Repository and verification state

- Repo: https://github.com/ronot-creator/TriageBot; default branch main.
- Production last known commit: b866af600cce323521faec78dbb53219aed633a7.
- Active PR: #11, “Add grounded private Q&A and harden triage safeguards”, branch fix/audit-announcement-fallback-help, open and unmerged; see PR #11 for the live head SHA.
- Latest verified executable-code CI: Actions run 39144786550 (https://github.com/ronot-creator/TriageBot/actions/runs/39144786550) passed compileall and 48 unit tests on the code/test commit. The subsequent documentation-only refresh also passed CI in run 39144852176 (https://github.com/ronot-creator/TriageBot/actions/runs/39144852176), with the same 48 tests. Tests mock external services and do not prove live Telegram, Gemini, Turso or Render integration.
- An older PR description referenced 39 tests; the latest verified branch count is 48. Re-fetch GitHub state before relying on this snapshot.
- PR #11 is intentionally not merged. Commits on this branch do not update production. Never merge, deploy, change Render settings or modify credentials without explicit owner approval.
- leader_chat_id remains unconfigured until the owner uses /setleaders. Do not assume current values for optional news/discussion/student group variables; inspect Render without exposing secret values.

## Complete tracked file inventory on PR #11 branch

1. **.env.example** — safe blank template for required Telegram/Gemini/Turso/owner variables and optional owner/leader IDs, model/quota settings, media/context bounds, group/channel IDs, FAQ settings and local DB toggle. No real secrets.
2. **.github/workflows/python-checks.yml** — GitHub Actions CI: checkout, Python 3.12 setup, install requirements, compile Python files, run unittest discovery. PR branch uses checkout v5/setup-python v6 and enables pushes to fix/** as well as PR checks.
3. **README.md** — operator setup, environment configuration, private Q&A, additive DB schema behavior, generic timetable upload/edit workflow, announcement review, workbook import, model policy and tests. Updated on PR #11 to describe direct private Q&A and selective leader updates.
4. **TIMETABLE_AI_RULES.md** — source-of-truth constraints for first-year timetable Q&A: DB only, explicit aliases, exact group matching, preserve shared-room ambiguity and source uncertainty, separate exams, report no match rather than claiming no real-world event exists.
5. **TIMETABLE_IMPORT.md** — instructions to upload workbook and configure Render/import verification. Known mismatch: it recommends running import_timetable.py before bot.py, while bot.py also imports the workbook at startup. Upsert is intended to be idempotent, but this duplicates work.
6. **assets/.gitkeep** — placeholder preserving the assets folder.
7. **assets/Medical_Timetable_FirstYear_Complete.xlsx** — first-year workbook with sheets Timetable, Translation Rules, Exam Timetable, AI Interpretation Rules. Documented expected source counts are 382 timetable rows and 9 exam rows; verify actual DB counts before relying on them.
8. **bot.py** — primary runtime and orchestration (~2,300 lines on this branch): environment validation, Turso client/schema, quotas, local triggers, context, leader permissions, Gemini triage/private Q&A, ticket/FAQ/blacklist/keyword commands, activity update parsing, timetable and announcement commands/callbacks, health server, Telegram handlers and polling startup.
9. **college_data.py** — schema/data access for conversation history, generic timetable upload previews/imports/entries/changes and announcement records. Parses CSV/XLSX/XLSM, validates rows, stores preview/warnings, requires leader action to commit, audit-logs manual edits and supplies announcement helpers.
10. **import_timetable.py** — idempotent first-year workbook importer. Creates and upserts timetable_events, exam_events, translation_aliases and interpretation_rules with stable SHA-256 source keys. CLI: python import_timetable.py "assets/Medical_Timetable_FirstYear_Complete.xlsx".
11. **requirements.txt** — requests, openai, python-telegram-bot 22.x, google-genai, libsql-client, Pydantic 2.x and openpyxl 3.1.x. Retain requests/openai unless usage is audited and the owner approves removal.
12. **tests/test_bot_contracts.py** — mocked client/database contract tests for model allowlisting, daily quotas, message deduplication, context identity, owner blacklist protection, local activity-update prefilter, leader-group authorization, private Q&A and source citation checks.
13. **tests/test_timetable_parser.py** — parser/validation tests for generic timetable uploads.
14. **tests/test_timetable_qa.py** — timetable intent, normalization, dates/groups/aliases, current-term handling and activity-status overlay tests including TBL cancellation.
15. **timetable_qa.py** — deterministic first-year timetable intent resolution and answers. Uses aliases and parameterized SQL against workbook timetable/exam records, applies leader activity updates and preserves unknown/ambiguous states.

The GitHub tree for this PR branch contained exactly the tracked paths listed above. No CONTEXT.md existed before this handover. No Composio integration, webhook server, Dockerfile, render.yaml, migration directory or separate SQLite session store was present. Do not claim any of those exist.

## What is functional versus verified

**Verified by code and mocked tests:** additive schema creation; local-first group triggers; two-model allowlist and per-model daily reservations; message deduplication; ticket/FAQ/blacklist/config tables; owner blacklist protection; private Q&A flow and source-ID validation; deterministic timetable-first private answers; activity keyword prefilter; leader-only /setleaders; generic timetable parsing; timetable intent/overlay tests; health routes; polling entry point. PR #11 CI passed compileall and 43 tests at the snapshot SHA.

**Not proven live:** Telegram message visibility/permissions and delivery; current Render settings/deployed SHA; actual Turso schema/data/counts; live Gemini free quota/model availability; private-message restart behavior; news channel admin permissions/publication; UptimeRobot status; end-to-end persistence across production redeploy. Do not describe mocked tests as production integration tests.

# Known Technical Debt & ChatGPT-Free Code Smells

Observed issues and risks to test; do not perform a wholesale rewrite before reproducing behavior.

1. **Monolithic bot.py:** orchestration, schema, model prompts, commands, health server and startup are mixed in one large module. Refactor incrementally with tests preserving handler order.
2. **Two timetable import/data paths:** generic leader-approved uploads use timetable_imports, timetable_entries, timetable_changes; workbook Q&A uses timetable_events, exam_events, translation_aliases, interpretation_rules. They are separate today but easy to confuse. Document which commands/answers read which model; do not unify without migration/rollback design.
3. **Third status layer:** activity_changes overlays workbook events. Test activity identity matching, effective dates, cancellation, correction and supersession. Older active records must not override newer confirmed ones.
4. **Duplicate workbook import startup path:** TIMETABLE_IMPORT.md suggests import then start bot, but bot.py imports on startup too. Choose one authoritative method and update docs/code.
5. **Substring keyword matching:** matched_keyword uses normalized substring containment. It can false-match inside words or miss variants. Add English word boundaries and Arabic normalization/variant tests; do not add generic words such as “lecture” as global triggers.
6. **Overlapping intent logic:** looks_like_college_schedule_question in bot.py and looks_like_schedule_question/intent resolution in timetable_qa.py have related responsibilities. Keep distinction clear and add shared regression cases.
7. **Leader update prefilter is heuristic:** activity term plus status/date/time cue identifies candidates only. It does not establish truth. Test negation, tentative language, group IDs, dates, time ranges, Arabic variants and correction messages. Never treat a student claim as official.
8. **Prompt role is not security:** sender_role passed to Gemini is informational only. Every sensitive write must independently enforce numeric ID/chat membership in Python.
9. **Duplicated model request logic:** triage, announcement classification, private Q&A and activity updates each contain some model selection, quota, JSON parsing, fallback and logging logic. Consolidate only after tests; ensure each attempted model is separately quota-accounted and logged.
10. **RPM limiter is process-local:** AI_RATE_LOCK/queues do not coordinate multiple Render instances. Daily counters are in Turso, but RPM is not distributed. Keep one instance unless addressed.
11. **Synchronous DB client in async app:** some DB calls use asyncio.to_thread; audit all handlers/loops for blocking direct calls.
12. **Synchronous startup work:** schema setup and workbook import can delay readiness. Ensure exceptions leave readiness false and deployment restart behavior is understood.
13. **Private /morehelp state may be volatile:** pending escalation flag lives in framework user context; restart may lose it. Persist with TTL if restart resilience is required.
14. **In-memory context is volatile:** CHAT_CONTEXT only contains messages seen since process start. Durable conversation_messages must be scoped correctly; never include another private user's history.
15. **Retention incomplete:** processed_messages is cleaned after 30 days at startup; define retention/archival for private history, request logs, tickets, announcements and clarifications. Earlier desired review horizon was around three months, but confirm policy before implementation.
16. **Quota accounting:** daily quota is reserved before a request, so failed attempts may consume app allowance. Verify aggregate api_usage and per-model model_usage semantics, including fallback.
17. **Environment validation:** numeric settings are parsed at startup; malformed values may crash startup. Clamp FAQ_CONFIDENCE_THRESHOLD to [0,1] and add safe errors.
18. **Logging/PII:** request log includes a session key derived from chat ID. Avoid secrets, prompts, private question text and unnecessary identifiers; define access/retention.
19. **Partial success boundaries:** DB write followed by Telegram send failure must be retryable without duplicate tickets/messages. Check status transitions and delivery state.
20. **Idempotency state:** a message is claimed in processed_messages; failure after claiming may suppress retry for a message that never produced a result. Consider processing states/reclaim semantics.
21. **Media limits:** supports photo/video/voice/audio and image/audio/video documents within configured size. Test MIME spoofing, timeouts, malformed files, captions and media-only posts. PDF OCR is not implemented; timetable uploads are CSV/XLSX/XLSM.
22. **Announcement model is not authority:** uncertain/failing classification should stay pending for human review. Keep source text/date/link and validate approval permissions; never publish without approval.
23. **No live integration tests:** CI mocks clients. Add opt-in staging tests with a test bot and disposable Turso DB; never use production for destructive tests.
24. **Dependencies not fully locked:** requirements uses version ranges. Consider lock/constraints only after compatibility validation.
25. **Docs may drift from Render:** README, TIMETABLE_IMPORT.md and dashboard settings may disagree. Inspect live config before edits.
26. **Missing full-path leader update tests:** detector unit tests do not prove handler, authorization, clarification and Turso write sequence. Add mocked handler tests, student rejection, ordinary chatter/no AI, source attribution and duplicate update cases.
27. **Privilege tiers:** LEADER_TELEGRAM_IDS and creator/admin status in the configured leader group grant leader access. Review owner-only versus leader-level commands. /setleaders stays owner-only and must run inside the intended group.
28. **No Composio code:** no client, connector, event trigger, callback or schema exists. Do not invent one.
29. **No webhook code:** deployment is polling. Do not configure setWebhook unless intentionally migrating and testing.
30. **No infrastructure manifest:** no Dockerfile or render.yaml; Render settings are dashboard-managed unless owner approves infrastructure-as-code.
31. **Branch/production distinction:** never infer production behavior from PR commits. Always cite exact SHA tested/deployed.
32. **No “100% production-ready” claim:** mocked tests establish only covered behavior; live integration and operational configuration must be independently verified.

# External Integrations & Auth Requirements

## Telegram

- Transport: library-managed long polling using getUpdates; no webhook URL/secret exists.
- Auth: TELEGRAM_BOT_TOKEN in Render environment. Owner must start bot in private chat before owner escalation works.
- Sender/role: update.effective_user.id, update.effective_chat.id/type and Telegram get_chat_member. Never trust username, display name or self-asserted role.
- Student group visibility depends on privacy mode and group permissions. Confirm before claiming all messages are visible.
- Leader group is not configured yet. Owner runs /setleaders inside it. Never run this for the owner.
- News channel: bot must be administrator; configure numeric NEWS_CHANNEL_ID. Publication target: numeric DISCUSSION_GROUP_ID. All eligible groups are monitored; the configured leaders' discussion is observed and persisted but excluded from student triage.
- Payloads are standard Telegram Update JSON handled by python-telegram-bot; no custom inbound payload. Telegram does not backfill old channel history automatically.
- Avoid exposing bot-token-containing URLs in logs; HTTP request loggers are reduced to WARNING.

## Gemini

- Auth: GEMINI_API_KEY in Render environment only.
- SDK: genai.Client(api_key=GEMINI_API_KEY), async calls through gemini_async.models.generate_content.
- Models: only gemini-3.5-flash-lite and gemini-3.1-flash-lite; 15 RPM and 500 RPD maximum per model. No paid provider may be introduced.
- Structured models: TriageResult, ActivityUpdateProposal, PrivateAnswer. Validate output in Python; source IDs must map to real evidence before database answers are returned.
- Group trigger contract: chatter is inspected locally first; only mention, reply-to-bot, keyword, schedule intent or authorized leader-update candidate enters an AI path. Private text Q&A is a separate intentional direct-answer path.
- Free-tier quota is not guaranteed by the local limiter. Retry only through approved fallback; no unbounded retries.

## Turso/libSQL

- Auth: TURSO_DATABASE_URL and TURSO_AUTH_TOKEN. Accepted schemes: libsql:// and https://. Never print token values.
- Schema initialization is additive and idempotent. Back up before schema changes; do not drop/recreate production tables.
- Tables declared in bot.py:
  - config(key,value): persistent configuration including leader_chat_id and current term.
  - smart_keywords(id,keyword,active,created_by,created_at): local trigger phrases.
  - blacklist(user_id,reason,added_by,created_at): excluded users; owner cannot be blacklisted.
  - faqs(id,question,answer,keywords,active,created_by,created_at,updated_at,last_used_at,last_reviewed_at): FAQ store.
  - tickets(id,source_chat_id,source_message_id,user_id,username,urgency,summary,reason,original_text,status,created_at,leader_message_id,claimed_by,resolved_by,resolved_at), unique by source chat/message.
  - api_usage(usage_date,requests): aggregate application request count.
  - model_usage(usage_date,model_name,requests), composite primary key: per-model daily reservations.
  - model_request_log(id,usage_date,provider,model_name,outcome,duration_ms,error_category,session_key,created_at): outcome/latency/error metadata, not raw prompt.
  - processed_messages(chat_id,message_id,processed_at), composite key: duplicate-processing guard.
  - activity_changes(id,activity_key,activity_type,status,effective_start,effective_end,details,related_activity,source_chat_id,source_message_id,created_by,created_at,updated_by,updated_at,active): leader activity updates.
  - pending_clarifications(user_id,chat_id,original_text,proposal_json,question,created_at,updated_at), composite key: pending clarification.
- Tables declared in college_data.py:
  - conversation_messages: persistent user/assistant/group context; private history must be scoped by user/chat.
  - timetable_imports: upload metadata/hash/preview/warnings/approval.
  - timetable_entries: generic normalized schedule sessions and source rows.
  - timetable_changes: audit log for generic timetable edits.
  - announcements: original source chat/message/date/link/text, classification, draft, review/publication state and timestamps.
- Tables declared in import_timetable.py:
  - timetable_events: workbook-derived first-year schedule and provenance, stable source key.
  - exam_events: separate assessment rows; do not infer missing types.
  - translation_aliases: explicit known aliases.
  - interpretation_rules: workbook interpretation constraints.
- No separate Redis/session service. pending_clarifications and conversation_messages persist in Turso; framework user_data and CHAT_CONTEXT are in-process. Persist restart-critical state explicitly with user/chat scoping and expiry.

## Render and monitoring

- Known URL: https://triagebot-wrzu.onrender.com.
- Required env names: TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, OWNER_TELEGRAM_ID.
- Optional env names: OWNER_USERNAME, LEADER_TELEGRAM_IDS, GEMINI_MODEL, GEMINI_FALLBACK_MODEL, GEMINI_PER_MODEL_RPM_LIMIT, GEMINI_PER_MODEL_RPD_LIMIT, MAX_MEDIA_MB, CONTEXT_MESSAGES, AUTO_FAQ_REPLY, FAQ_CONFIDENCE_THRESHOLD, ALLOW_LOCAL_DB, NEWS_CHANNEL_ID, DISCUSSION_GROUP_ID, TIMETABLE_WORKBOOK_PATH, PORT, LOG_LEVEL.
- Intended build: pip install -r requirements.txt; intended start: python bot.py. Verify actual dashboard config before changing.
- GET / and GET /health/live return 200; GET /health/ready returns 200 when ready, 503 during initialization/failure. UptimeRobot was set to check readiness every five minutes.
- Health server binds 0.0.0.0:$PORT as required for a Render web service. It exposes status routes only.
- Render environment changes may restart/redeploy. Do not deploy, edit variables or rotate secrets without explicit permission. Do not scale to multiple polling instances without solving duplicate polling and distributed RPM limits.

## Composio

No Composio client, connector, trigger, action, callback, configuration or expected payload exists in this repository. Composio is not integrated. Do not invent expected I/O. If added later with approval, document connector, event/action, scopes, input/output schema, idempotency, retries and failure behavior first.

# Next Execution Objectives (Ordered by Priority)

The next agent should work on the current PR branch unless explicitly told otherwise. Do not merge/deploy PR #11, change credentials, configure the leader group, or perform destructive/production tests without explicit approval.

## P0 — Re-establish baseline

- [ ] Fetch current main, PR #11, branch head, changed files and CI status; do not assume this snapshot remains current.
- [ ] Read all of bot.py, college_data.py, timetable_qa.py, import_timetable.py, docs and tests before edits.
- [ ] Inspect Render service type, build/start command, deployed SHA, readiness and environment-variable names without printing values.
- [ ] Verify whether the saved Turso-token change was deployed; do not change/rotate it.
- [ ] Treat 43 passing mocked tests as branch baseline only.
- [ ] Preserve zero-spend policy and only the two approved Gemini models.

## P1 — Prove selective detection and authorization

- [ ] Write a routing matrix for ordinary chatter, keyword, schedule question, mention/reply, media-only message, authorized leader update, student rumor, private Q&A and channel post.
- [ ] Test ordinary chatter causes zero Gemini calls; triggered messages use one logical workflow, with only bounded approved fallback attempts.
- [ ] Test keyword boundaries and Arabic/English variants; avoid generic trigger words.
- [ ] Test full leader update flow: authorized leader candidate -> parser; same student message -> never official; ambiguous -> saved clarification; clear proposal -> validated Turso write; unrelated leader chatter -> no AI.
- [ ] Verify sender IDs, leader-group membership and owner-only actions at every write/callback path. Prompt text is not a permission check.
- [ ] Test handler order and ApplicationHandlerStop so leader updates are not also student-triaged.

## P2 — Persistence, quota and async reliability

- [ ] Audit direct synchronous DB calls in async handlers; use thread offload where needed.
- [ ] Test Turso outage, malformed AI JSON, quota exhaustion, Telegram send failure and restart. Prevent duplicate tickets and silent loss after message claim.
- [ ] Verify fallback quota reservations/logging for both models and aggregate/per-model counters.
- [ ] Decide whether /morehelp pending state must survive restart; persist with TTL if needed.
- [ ] Test private context isolation, bounded retention and recovery after restart.
- [ ] Define retention for messages, logs, tickets, announcements and clarifications; preserve leader audit history.
- [ ] Validate numeric env settings and clamp threshold values with safe startup errors.

## P3 — Timetable and announcement correctness

- [ ] Use a disposable Turso DB to test workbook idempotency and counts (documented expected source rows: 382 timetable, 9 exams); do not run destructive tests in production.
- [ ] Verify parameterized SQL, exact group IDs, aliases, relative dates, term boundaries, room ambiguity and missing-data responses.
- [ ] Test activity update ordering, effective dates, cancellation, reinstatement and correction/supersession; stale active records must not override newer confirmed records.
- [ ] Document distinction between generic timetable tables and workbook Q&A tables; do not merge without migration/rollback.
- [ ] Choose one workbook import path and reconcile TIMETABLE_IMPORT.md with startup behavior.
- [ ] Keep uncertain announcement classification pending; only authorized approval can publish; deduplicate channel updates.

## P4 — Security and operations

- [ ] Audit all commands/callbacks for permissions, stale callbacks and owner blacklist protection.
- [ ] Escape user-controlled HTML, cap/split Telegram messages safely and test oversized responses.
- [ ] Remove secrets/prompts/private question contents from logs; minimize chat/user identifiers.
- [ ] Verify Telegram privacy mode and actual bot message visibility with owner participation.
- [ ] Verify live health endpoints and readiness behavior.
- [ ] Keep one polling instance until distributed polling/rate limits are designed.

## P5 — Documentation, modularization and delivery

- [ ] Reconcile README, import instructions, .env.example and Render dashboard commands.
- [ ] Split bot.py only in small test-backed increments; preserve handler order.
- [ ] Audit requests/openai before suggesting removal.
- [ ] Add opt-in staging tests using a test bot and disposable Turso DB. Distinguish mocked CI, staging and production evidence.
- [ ] Run python -m compileall -q . and python -m unittest discover -s tests -v; inspect full logs and fix every failure.
- [ ] After commits, verify CI against exact head SHA. Do not use an older green run as evidence for newer code.
- [ ] Provide an evidence table mapping requirement, code location, test, result and remaining uncertainty. Never claim “100% production-ready” without live integration evidence.
- [ ] Leave PR #11 open/unmerged until explicit approval.

## Autonomous agent constraints

1. Inspect before editing; prefer minimal, auditable diffs.
2. Never print, rotate, replace or delete API keys, bot tokens or Turso credentials without explicit permission.
3. Never merge, deploy, change Render variables, configure leader_chat_id, or perform destructive production DB operations without explicit permission.
4. Never add paid services/providers or route outside the two approved Gemini models.
5. Treat model output as untrusted; validate schema, source, permissions and persistence in Python.
6. State exactly what was tested and what was not; mocked unit tests are not live integration proof.
7. Preserve Turso data and auditability; schema changes require backward-compatible migration and rollback/backup plan.
8. If code and this document disagree, inspect current code and document the discrepancy before changing behavior. This handover is context, not permission to bypass safeguards.


## 2026-10-18 follow-up repairs

- Replaced unsafe `evidence_text[:26000]` truncation with `_bounded_evidence_json`, which bounds evidence fields/collections before serialization and returns valid JSON. Added oversized-evidence and source-identifier regression tests.
- Removed the single-group `ALLOWED_GROUP_ID` allowlist. Eligible group messages are persisted across groups; the configured leaders' discussion is observed/persisted but excluded from student triage to avoid creating tickets in the internal leadership chat. Dedicated leader commands and clarification handling remain active. Added regression tests for group inclusion and the leader-chat boundary.
- Global Telegram error handler now logs only the exception type instead of the exception message/traceback, reducing the risk of request URLs or secrets appearing in logs. Added a redaction regression test.
- Latest verified CI on `9e6a4e66cbd96ff110e4d4bdf7c5a8cfe285f2df`: compileall passed and 48 unit tests passed. These are mocked CI tests, not live integration proof.
- Production was not modified or redeployed. Render health-check configuration, credential rotation, live Turso verification, actual Telegram privacy-mode visibility, and owner `/setleaders` setup remain outstanding.
