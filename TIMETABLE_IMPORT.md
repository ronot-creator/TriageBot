# First-year timetable import to Turso

The Excel workbook is a source file; Turso stores its sheets as tables. This script imports Timetable, Exam Timetable, Translation Rules, and AI Interpretation Rules into timetable_events, exam_events, translation_aliases, and interpretation_rules.

## 1. Download the workbook
Download Medical_Timetable_FirstYear_Complete.xlsx from the ChatGPT conversation and save it locally, for example in Downloads. Do not commit credentials or .env files to GitHub.

## 2. Install dependencies
Run: python -m pip install -r requirements.txt

## 3. Set Turso credentials and run the import

Windows Command Prompt:
```bat
set TURSO_DATABASE_URL=libsql://YOUR-DATABASE-YOUR-ORG.turso.io
set TURSO_AUTH_TOKEN=YOUR_DATABASE_TOKEN
python import_timetable.py "%USERPROFILE%\Downloads\Medical_Timetable_FirstYear_Complete.xlsx"
```

PowerShell:
```powershell
$env:TURSO_DATABASE_URL="libsql://YOUR-DATABASE-YOUR-ORG.turso.io"
$env:TURSO_AUTH_TOKEN="YOUR_DATABASE_TOKEN"
python import_timetable.py "$HOME\Downloads\Medical_Timetable_FirstYear_Complete.xlsx"
```

Replace example credentials with values from the existing Turso database dashboard. Never paste the token into GitHub, Telegram, or chat. The importer accepts libsql:// and https:// URLs and uses stable keys/upserts to avoid duplicating identical rows on repeat imports.

## 4. Verify in the Turso SQL console
```sql
SELECT COUNT(*) FROM timetable_events;
SELECT COUNT(*) FROM exam_events;
SELECT COUNT(*) FROM translation_aliases;
SELECT COUNT(*) FROM interpretation_rules;
SELECT event_date,day,start_time,end_time,subject,activity_type,groups_text,room_text
FROM timetable_events ORDER BY event_date,start_time LIMIT 10;
SELECT event_date,day,assessment_type,subject,review_note FROM exam_events ORDER BY event_date;
```

Expected processed source rows: 382 timetable events, 9 exam events, 19 translation-rule rows, and 12 interpretation-rule rows. Existing database tables may contain more aliases/rules if you add approved entries.

Important: importing the data does not alone make the bot answer timetable questions. Runtime intent recognition, alias lookup, SQL filtering, and answer generation must be wired into the bot separately. Keep room-sharing and unresolved-time caveats intact.
