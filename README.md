# TriageBot

Telegram college triage bot with durable Turso/libSQL storage.

## Environment variables

Required: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, and numeric `OWNER_TELEGRAM_ID`.

Use `LEADER_TELEGRAM_IDS` for a comma-separated allowlist of trusted leader user IDs. `OWNER_USERNAME` is an optional public Telegram username (without `@`) shown in student help text; private `/morehelp` messages are delivered to `OWNER_TELEGRAM_ID`. This is safer than treating every member of the leaders' group as a privileged user; group creators/administrators are also recognized.

Optional existing settings and the new college-data settings are documented in [`.env.example`](.env.example). Put real values in Render's environment settings, not in GitHub. `NEWS_CHANNEL_ID` and `DISCUSSION_GROUP_ID` must be numeric Telegram chat IDs, not invite links.

## Student help and private support

`/help` shows the bot’s purpose, supported timetable/FAQ/announcement capabilities, and usage guidance. In a private chat, ordinary text questions are answered directly: timetable/TBL queries use deterministic database-backed logic; other college-specific questions use active activity updates, active FAQs, approved/published announcements, and imported timetable records as evidence. The AI must not invent college facts; it must report missing or conflicting information and validate FAQ, announcement, activity-update, imported timetable, or exam source identifiers before presenting a model-generated database answer. General-knowledge answers are labelled as general rather than official college information. Private conversation history is stored by private chat and user, and is not mixed with other users’ histories. Use `/morehelp` when you explicitly want to forward a message to the configured owner Telegram account (`OWNER_TELEGRAM_ID`); after `/morehelp`, the next text message is forwarded to the owner instead of being answered by AI. The owner must have opened the bot and pressed Start at least once so Telegram permits the bot to initiate a private message. Set optional `OWNER_USERNAME` to the owner’s public username (without `@`) to display it in help. Ordinary private questions are not posted to group chats.

## Database migration

On startup, the bot creates missing tables and indexes with `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`. Existing tables and records are not dropped or recreated. Back up the Turso database before deploying any schema-changing release.

New tables:
- `conversation_messages`: bounded persistent context, queried per chat and user so separate users' histories are not mixed.
- `timetable_imports`: source-file hash, uploader, import preview, validation warnings and approval state.
- `timetable_entries`: normalized date/weekday, start/end time, course, session type, location, instructor, status and source row.
- `timetable_changes`: audit log of leader edits.
- `announcements`: original news post, source link/date, classification, draft, review state and publication metadata.

## Importing a timetable

1. Add the bot to the leaders' group and link it using `/setleaders` as the owner.
2. Upload a `.csv`, `.xlsx`, or `.xlsm` timetable file in that leaders' group.
3. Review the preview and validation warnings. No active timetable entries are written until a leader chooses an action:
   - **Add valid rows** keeps existing active records and adds these sessions.
   - **Replace active timetable** marks all previous scheduled records as superseded and records the change audit; use this only when the uploaded file is the complete replacement timetable.
   - **Cancel import** leaves the active timetable unchanged.
4. Search with `/timetable anatomy` or `/timetable Hall 2`.
5. Edit a record with `/timetablechange ID | location=Hall 3 | reason=official correction`. Supported fields: `course`, `session_type`, `session_date` (YYYY-MM-DD), `weekday`, `start_time` and `end_time` (HH:MM), `location`, `instructor`, `notes`, and `status` (`scheduled`, `cancelled`, `superseded`). Changes are recorded in `timetable_changes`.

The importer deliberately rejects unknown headers, unparseable dates/times, and unsupported formats instead of guessing. For the most reliable import, use one row per session and explicit columns such as Course, Date or Weekday, Start, End, Location, Instructor and Type. CSV/XLSX are supported now; PDF/image OCR is not enabled yet. Review the preview before approving, especially when the source uses merged cells or multi-row headers.

## Announcement review

1. Add the bot as an administrator to the news channel so it receives new channel posts.
2. Set `NEWS_CHANNEL_ID` and `DISCUSSION_GROUP_ID` in Render.
3. Relevant-looking posts are saved with their original text and source link and sent to the leaders as drafts. Irrelevant posts are marked ignored.
4. Leaders use `/pendingannouncements`, then `/approveannouncement ID`, `/editannouncement ID | corrected summary`, or `/rejectannouncement ID`.
5. Summaries are only posted to the configured discussion group after explicit approval.
6. When a student asks a triggered question, the bot may answer from relevant saved announcement records and include the source date/link. If no saved announcement supports the answer confidently, it falls back to the existing human-review flow.

To find IDs: run `/chatid` as the owner inside the discussion group; set that value as `DISCUSSION_GROUP_ID`. The bot monitors eligible groups where it is installed; it does not require a single student-group allowlist. For the news channel, add the bot as an administrator and publish a test post while `NEWS_CHANNEL_ID` is blank; the Render log records the numeric channel ID, which you can then add to the environment and redeploy.

Telegram bot updates do not provide automatic backfill of all old channel history; ingestion starts with posts/edits received after the bot is correctly configured. Treat AI summaries as drafts and verify dates, places and times against the original post.

## Testing

Run `python -m compileall -q .` and `python -m unittest discover -s tests -v`. GitHub Actions runs these checks on pushes to `main` and pull requests.

## First-year timetable workbook (source-grounded Q&A)

The dedicated workbook importer creates `timetable_events`, `exam_events`, `translation_aliases`, `interpretation_rules`. It is separate from the leader-approved generic `timetable_entries` import flow.

To load the supplied workbook using the web-only workflow, upload `Medical_Timetable_FirstYear_Complete.xlsx` to `assets/` on the deployed branch via GitHub's web UI. The bot imports this workbook automatically during startup when the file exists, before Telegram polling begins; set Render's Start Command to:

```sh
python bot.py
```

The startup import is idempotent, and a failed import stops startup rather than serving a partial timetable. You can still run `python import_timetable.py assets/Medical_Timetable_FirstYear_Complete.xlsx` manually for a deliberate one-off import. See [TIMETABLE_IMPORT.md](TIMETABLE_IMPORT.md). Timetable questions use deterministic alias resolution and parameterized SQL; the model is not allowed to invent schedule facts or generate SQL. Group IDs are matched exactly, and room-sharing/source uncertainty is preserved.

## Gemini model policy

Only `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite` are accepted. The default is 3.5 Flash-Lite with 3.1 Flash-Lite as fallback. Per-model limits default to 15 requests/minute and 500 requests/day; application settings clamp them to those maximums. Google enforces token-per-minute limits at the API. These controls do not guarantee a free quota is available, and a provider quota response still triggers fallback when possible.


### Automatic leader activity updates (selective AI)
- Ordinary group messages are checked locally first; routine chatter does not call Gemini.
- In the configured leadership group, only authorized leaders' messages that match a local activity + status/date/time pattern enter the existing structured activity-update parser.
- Ambiguous updates are saved as pending clarifications, not as confirmed timetable changes. Clear validated updates are stored in Turso with the source chat/message and leader ID.
- Student messages never become official timetable changes merely because they contain a keyword; they continue through the discussion-group triage/verification path.
- Keyword detection is a pre-filter, not proof. The parser must not invent missing dates, groups, or statuses.
