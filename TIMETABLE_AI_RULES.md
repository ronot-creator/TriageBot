# First-year timetable interpretation rules

- Scope is first-year students only. Do not answer for other years without another dataset.
- Schedule facts must come from database rows, not model memory. Convert the request to structured filters in code, query the database, then give the AI only matching rows and relevant notes.
- Resolve only exact or approved aliases in translation_aliases. Add Arabic-script phonetic spellings when students actually use them; do not invent transliterations.
- Canonical subjects include Anatomy, Histology, Biochemistry, Physiology, and Vertical where present.
- Lecture, Practical, GD, TBL, and PG are distinct activity types. “Section” is a broad search term for non-lecture activities, not a synonym for Practical.
- Match complete group IDs. Group 4 must never match Group 14. Never assume a group if the student did not specify one.
- Preserve all listed rooms. A shared-room note is not proof of a one-to-one group-room assignment. Do not invent room assignments.
- If multiple events match, show the alternatives or ask one concise clarification.
- Verification status, review note, source page/cell, and source text are provenance fields. They are not the answer itself, but relevant review notes must be consulted.
- The Physiology lecture on 17 November has unusual source text “12 – 10.5”; the workbook normalizes it to 10:00–12:00 but flags it unresolved. Do not present it as fully confirmed.
- Use stored dates and local timetable times. Do not silently correct date/weekday conflicts.
- Query exam_events separately from timetable_events. Do not merge exams into the ordinary class timetable. Preserve “Not specified” where assessment details are unclear.
- If no matching row exists, say no matching entry was found in the imported dataset; do not claim no event exists in reality.
- Give the direct answer first, then date, time, activity, group, and room when available. Be explicit about uncertainty.
