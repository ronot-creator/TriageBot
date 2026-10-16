# First-year timetable interpretation rules

- Scope: first-year students only.
- The database is the authority for schedule facts; never answer from model memory.
- Interpret known aliases, then use code and parameterized SQL to query Turso. The LLM must not generate SQL.
- Only use aliases explicitly present in translation_aliases. Add Arabic-script phonetic spellings after students actually use them; do not invent transliterations.
- Canonical subjects include Anatomy, Histology, Biochemistry, Physiology, and Vertical where present.
- Lecture, Practical, GD, TBL, and PG are distinct activity types. “Section” is a broad search for non-lecture activities, not a synonym for Practical.
- Match full group IDs. Group 4 must not match Group 14.
- Preserve every listed room. A shared-room note is not proof of a one-to-one group-room assignment.
- Read review notes and disclose unresolved entries, including the Physiology lecture on 17 November with source text “12 – 10.5”; do not claim its normalized time is fully confirmed.
- Keep exam_events separate from timetable_events. Do not upgrade “Not specified” or “Ambiguous — confirm” into a specific assessment type.
- If multiple results are plausible, show alternatives. If no row matches, say no matching entry was found in the imported dataset; don't claim that no real-world event exists.
- Dates and times are local timetable values. The code parses date/time constraints; the model does not invent or silently correct them.
- Verification status, source page/cell, source text, and review notes are provenance. Use notes to explain uncertainty, not as a replacement for event fields.
