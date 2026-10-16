# Import the first-year workbook on Render

The bot does not need to be installed on your computer. Render installs the Python packages in requirements.txt during its build.

## 1. Put the workbook in the repository using GitHub's website
1. Open this feature branch in GitHub.
2. Create a folder named assets if it does not exist.
3. Use Add file -> Upload files.
4. Upload Medical_Timetable_FirstYear_Complete.xlsx into assets/.
5. Commit the upload to the same feature branch. The workbook contains timetable data, not credentials.

Expected repository path: assets/Medical_Timetable_FirstYear_Complete.xlsx

## 2. Configure Render
In the Render Web Service settings, set the Build Command to:

```sh
pip install -r requirements.txt
```

Set the Start Command to:

```sh
python import_timetable.py assets/Medical_Timetable_FirstYear_Complete.xlsx && python bot.py
```

The importer runs before the bot starts. It creates the timetable/exam/alias/rule tables and upserts the workbook rows. If import fails, the && prevents the bot from starting with a silently incomplete import. The same workbook can be imported on subsequent restarts.

Keep the existing required environment variables in Render:
- TELEGRAM_BOT_TOKEN
- GEMINI_API_KEY
- TURSO_DATABASE_URL
- TURSO_AUTH_TOKEN
- OWNER_TELEGRAM_ID

Do not put secrets in the repository or workbook. The service must use the existing Turso database so its current FAQ/ticket/config tables remain intact.

## 3. Verify in Turso's web SQL console

```sql
SELECT COUNT(*) AS timetable_rows FROM timetable_events;
SELECT COUNT(*) AS exam_rows FROM exam_events;
SELECT COUNT(*) AS alias_rows FROM translation_aliases;
SELECT COUNT(*) AS rule_rows FROM interpretation_rules;
SELECT event_date,day,start_time,end_time,subject,activity_type,groups_text,room_text
FROM timetable_events ORDER BY event_date,start_time LIMIT 10;
```

Expected workbook source counts: 382 timetable rows and 9 exam rows. Translation aliases and interpretation rules are imported from their sheets. Existing table totals can be higher if you add approved aliases/rules.

## Important
The runtime timetable handler uses parameterized SQL and only answers from rows returned by Turso. If the import tables are missing, it says the timetable service is unavailable rather than guessing. Do not merge/deploy until the GitHub checks pass and you have verified these counts in Turso.
