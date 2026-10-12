"""Persistent college data helpers for TriageBot.

All schema changes are additive. Timetable uploads are parsed into a preview first;
call commit_timetable_import only after an authorized human confirms the preview.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, time
from typing import Any

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS conversation_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        user_id INTEGER,
        role TEXT NOT NULL CHECK(role IN ('user','assistant')),
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(chat_id, message_id, role)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_conversation_chat_order ON conversation_messages(chat_id, id)",
    """CREATE TABLE IF NOT EXISTS timetable_imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_name TEXT NOT NULL,
        file_sha256 TEXT NOT NULL,
        uploaded_by INTEGER NOT NULL,
        uploaded_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        parsed_rows_json TEXT NOT NULL,
        errors_json TEXT NOT NULL DEFAULT '[]',
        confirmed_by INTEGER,
        confirmed_at TEXT,
        UNIQUE(file_sha256, status)
    )""",
    """CREATE TABLE IF NOT EXISTS timetable_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        import_id INTEGER NOT NULL,
        row_number INTEGER NOT NULL,
        course TEXT NOT NULL,
        session_type TEXT NOT NULL DEFAULT '',
        session_date TEXT,
        weekday TEXT,
        start_time TEXT,
        end_time TEXT,
        location TEXT NOT NULL DEFAULT '',
        instructor TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'scheduled',
        source_file TEXT NOT NULL,
        source_row_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(import_id, row_number),
        FOREIGN KEY(import_id) REFERENCES timetable_imports(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_timetable_date_time ON timetable_entries(session_date, start_time)",
    "CREATE INDEX IF NOT EXISTS idx_timetable_weekday_time ON timetable_entries(weekday, start_time)",
    "CREATE INDEX IF NOT EXISTS idx_timetable_course ON timetable_entries(course)",
    """CREATE TABLE IF NOT EXISTS timetable_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL,
        changed_by INTEGER NOT NULL,
        changed_at TEXT NOT NULL,
        old_values_json TEXT NOT NULL,
        new_values_json TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(entry_id) REFERENCES timetable_entries(id)
    )""",
    """CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_chat_id INTEGER NOT NULL,
        source_message_id INTEGER NOT NULL,
        source_date TEXT NOT NULL,
        source_link TEXT NOT NULL DEFAULT '',
        original_text TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'unclassified',
        relevance TEXT NOT NULL DEFAULT 'pending_review',
        draft_summary TEXT NOT NULL DEFAULT '',
        approved_summary TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending_review',
        first_seen_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        reviewed_by INTEGER,
        reviewed_at TEXT,
        published_message_id INTEGER,
        UNIQUE(source_chat_id, source_message_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_announcements_status_date ON announcements(status, source_date)",
]


def init_college_schema(db: Any) -> None:
    for sql in SCHEMA:
        db.execute(sql)


def _norm_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s_\-/]+", " ", text)
    return text


ALIASES = {
    "course": {"course", "subject", "lecture", "module", "المادة", "اسم المادة", "المقرر", "المحاضرة", "اسم المحاضرة"},
    "date": {"date", "day date", "session date", "التاريخ", "تاريخ", "اليوم والتاريخ"},
    "weekday": {"weekday", "day", "week day", "اليوم", "يوم"},
    "start": {"start", "start time", "from", "time from", "begin", "بداية", "من", "وقت البداية", "من الساعة"},
    "end": {"end", "end time", "to", "time to", "finish", "نهاية", "إلى", "الى", "وقت النهاية", "حتى"},
    "time": {"time", "time slot", "lecture time", "الوقت", "المواعيد", "ميعاد", "الساعة"},
    "location": {"location", "place", "room", "hall", "venue", "المكان", "القاعة", "مكان", "مدرج"},
    "type": {"type", "session type", "kind", "النوع", "نوع المحاضرة", "عملي نظري"},
    "instructor": {"instructor", "lecturer", "doctor", "teacher", "الدكتور", "المحاضر", "المعيد"},
    "notes": {"notes", "note", "details", "ملاحظات", "تفاصيل"},
}
WEEKDAYS = {
    "monday": "Monday", "mon": "Monday", "الاثنين": "Monday", "الإثنين": "Monday", "الاتنين": "Monday",
    "tuesday": "Tuesday", "tue": "Tuesday", "الثلاثاء": "Tuesday", "التلات": "Tuesday",
    "wednesday": "Wednesday", "wed": "Wednesday", "الأربعاء": "Wednesday", "الاربعاء": "Wednesday",
    "thursday": "Thursday", "thu": "Thursday", "الخميس": "Thursday",
    "friday": "Friday", "fri": "Friday", "الجمعة": "Friday",
    "saturday": "Saturday", "sat": "Saturday", "السبت": "Saturday",
    "sunday": "Sunday", "sun": "Sunday", "الأحد": "Sunday", "الاحد": "Sunday",
}


def _parse_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_time(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    # Excel stores times as fractions of a day.
    if isinstance(value, (int, float)) and 0 <= value < 1:
        mins = round(float(value) * 24 * 60)
        return f"{(mins // 60) % 24:02d}:{mins % 60:02d}"
    text = str(value).strip().replace(".", ":")
    text = re.sub(r"\s+", " ", text)
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
        try:
            return datetime.strptime(text.upper(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def _split_time_range(value: Any) -> tuple[str | None, str | None]:
    text = str(value or "").strip()
    parts = re.split(r"\s*(?:-|–|—|to|حتى)\s*", text, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return _parse_time(value), None
    return _parse_time(parts[0]), _parse_time(parts[1])


def _column_map(headers: list[Any]) -> dict[str, int]:
    normalized = [_norm_header(h) for h in headers]
    result: dict[str, int] = {}
    for field, aliases in ALIASES.items():
        for idx, header in enumerate(normalized):
            if header in aliases:
                result[field] = idx
                break
    return result


def _read_rows(file_name: str, payload: bytes) -> list[list[Any]]:
    lower = file_name.lower()
    if lower.endswith(".csv"):
        text = payload.decode("utf-8-sig")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [list(row) for row in csv.reader(io.StringIO(text), dialect)]
    if lower.endswith((".xlsx", ".xlsm")):
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise ValueError("Excel import requires openpyxl to be installed.") from exc
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        rows: list[list[Any]] = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                vals = list(row)
                if any(v is not None and str(v).strip() for v in vals):
                    rows.append(vals)
        workbook.close()
        return rows
    raise ValueError("Supported timetable formats are .csv, .xlsx, and .xlsm. PDF/image import needs a layout-specific review step.")


def parse_timetable(file_name: str, payload: bytes) -> dict[str, Any]:
    """Parse a timetable conservatively; unresolved rows are reported, never silently dropped."""
    if len(payload) > 10 * 1024 * 1024:
        raise ValueError("Timetable file exceeds 10 MB.")
    rows = _read_rows(file_name, payload)
    if len(rows) < 2:
        raise ValueError("No timetable rows found.")
    # Search the first 15 non-empty rows for a recognizable header.
    header_idx = None
    columns = {}
    for idx, row in enumerate(rows[:15]):
        candidate = _column_map(row)
        if "course" in candidate and ("start" in candidate or "time" in candidate):
            header_idx, columns = idx, candidate
            break
    if header_idx is None:
        raise ValueError("Could not confidently identify headers. Use columns for Course, Date or Weekday, Start/Time, End, and Location.")
    parsed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for source_idx, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        def get(field: str) -> Any:
            idx = columns.get(field)
            return row[idx] if idx is not None and idx < len(row) else None
        if not any(v is not None and str(v).strip() for v in row):
            continue
        course = str(get("course") or "").strip()
        raw_date = get("date")
        parsed_date = _parse_date(raw_date)
        raw_weekday = str(get("weekday") or "").strip().lower()
        weekday = WEEKDAYS.get(raw_weekday)
        raw_time = get("time")
        start = _parse_time(get("start"))
        end = _parse_time(get("end"))
        if raw_time and not start:
            start, range_end = _split_time_range(raw_time)
            end = end or range_end
        location = str(get("location") or "").strip()
        if not course:
            errors.append({"row": source_idx, "reason": "missing course/subject", "source": [str(x or "") for x in row]})
            continue
        if raw_date and not parsed_date:
            errors.append({"row": source_idx, "reason": f"unrecognized date: {raw_date}", "source": [str(x or "") for x in row]})
            continue
        if get("start") not in (None, "") and not start:
            errors.append({"row": source_idx, "reason": f"unrecognized start time: {get('start')}", "source": [str(x or "") for x in row]})
            continue
        if raw_time and not start:
            errors.append({"row": source_idx, "reason": f"unrecognized time: {raw_time}", "source": [str(x or "") for x in row]})
            continue
        if not parsed_date and not weekday:
            errors.append({"row": source_idx, "reason": "missing/unknown date or weekday", "source": [str(x or "") for x in row]})
            continue
        if not start:
            errors.append({"row": source_idx, "reason": "missing/unknown start time", "source": [str(x or "") for x in row]})
            continue
        if not location:
            warnings.append({"row": source_idx, "reason": "location/place is blank; verify before relying on this entry"})
        if not end:
            warnings.append({"row": source_idx, "reason": "end time is blank; verify session duration"})
        if not str(get("type") or "").strip():
            warnings.append({"row": source_idx, "reason": "session type is blank; source may not distinguish lecture/practical"})
        parsed.append({
            "row_number": source_idx,
            "course": course,
            "session_type": str(get("type") or "").strip(),
            "session_date": parsed_date,
            "weekday": weekday,
            "start_time": start,
            "end_time": end,
            "location": location,
            "instructor": str(get("instructor") or "").strip(),
            "notes": str(get("notes") or "").strip(),
            "source_row": [str(x.isoformat() if hasattr(x, "isoformat") else (x if x is not None else "")) for x in row],
        })
    if not parsed and not errors:
        raise ValueError("No usable timetable entries were detected.")
    return {"entries": parsed, "errors": errors, "warnings": warnings, "rows_seen": max(0, len(rows) - header_idx - 1)}


def save_timetable_preview(db: Any, file_name: str, payload: bytes, uploader_id: int, now: str) -> tuple[int, dict[str, Any]]:
    parsed = parse_timetable(file_name, payload)
    digest = hashlib.sha256(payload).hexdigest()
    existing = db.execute(
        "SELECT id, status FROM timetable_imports WHERE file_sha256=? AND status IN ('pending','confirmed') ORDER BY id DESC LIMIT 1",
        (digest,),
    )
    if existing.rows:
        raise ValueError(f"This exact file was already imported or is awaiting approval (import #{existing.rows[0][0]}, status={existing.rows[0][1]}).")
    result = db.execute(
        """INSERT INTO timetable_imports(file_name,file_sha256,uploaded_by,uploaded_at,status,parsed_rows_json,errors_json)
           VALUES (?,?,?,?, 'pending', ?, ?)""",
        (file_name[:255], digest, uploader_id, now, json.dumps(parsed["entries"], ensure_ascii=False), json.dumps(parsed["errors"], ensure_ascii=False)),
    )
    import_id = int(getattr(result, "last_insert_rowid", 0) or 0)
    if not import_id:
        row = db.execute("SELECT id FROM timetable_imports WHERE file_sha256=? AND status='pending' ORDER BY id DESC LIMIT 1", (digest,))
        if not row.rows:
            raise RuntimeError("Could not retrieve the created timetable import ID.")
        import_id = int(row.rows[0][0])
    return import_id, parsed


def commit_timetable_import(db: Any, import_id: int, actor_id: int, now: str) -> int:
    found = db.execute(
        "SELECT status, parsed_rows_json, file_name FROM timetable_imports WHERE id=?",
        (import_id,),
    )
    if not found.rows:
        raise ValueError("Import not found.")
    status, raw_rows, file_name = found.rows[0]
    if status != "pending":
        raise ValueError(f"Import is not pending (status={status}).")
    entries = json.loads(raw_rows)
    for entry in entries:
        db.execute(
            """INSERT OR IGNORE INTO timetable_entries
               (import_id,row_number,course,session_type,session_date,weekday,start_time,end_time,location,instructor,notes,status,source_file,source_row_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'scheduled',?,?,?,?)""",
            (import_id, entry["row_number"], entry["course"], entry["session_type"], entry["session_date"],
             entry["weekday"], entry["start_time"], entry["end_time"], entry["location"], entry["instructor"],
             entry["notes"], file_name, json.dumps(entry["source_row"], ensure_ascii=False), now, now),
        )
    db.execute(
        "UPDATE timetable_imports SET status='confirmed', confirmed_by=?, confirmed_at=? WHERE id=? AND status='pending'",
        (actor_id, now, import_id),
    )
    return len(entries)


def cancel_timetable_import(db: Any, import_id: int, actor_id: int, now: str) -> None:
    db.execute(
        "UPDATE timetable_imports SET status='cancelled', confirmed_by=?, confirmed_at=? WHERE id=? AND status='pending'",
        (actor_id, now, import_id),
    )


def list_timetable(db: Any, query: str = "", limit: int = 20) -> list[tuple]:
    pattern = f"%{query.strip()}%" if query.strip() else "%"
    result = db.execute(
        """SELECT id,course,session_type,session_date,weekday,start_time,end_time,location,instructor,status
           FROM timetable_entries
           WHERE status='scheduled' AND (course LIKE ? OR location LIKE ? OR instructor LIKE ? OR notes LIKE ?)
           ORDER BY COALESCE(session_date,'9999-12-31'), CASE weekday
             WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3
             WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 WHEN 'Sunday' THEN 7 ELSE 8 END,
             start_time LIMIT ?""",
        (pattern, pattern, pattern, pattern, max(1, min(100, limit))),
    )
    return list(result.rows)


def update_timetable_entry(db: Any, entry_id: int, changes: dict[str, str], actor_id: int, reason: str, now: str) -> None:
    allowed = {"course", "session_type", "session_date", "weekday", "start_time", "end_time", "location", "instructor", "notes", "status"}
    if not changes or set(changes) - allowed:
        raise ValueError("Invalid timetable fields.")
    current = db.execute(
        "SELECT course,session_type,session_date,weekday,start_time,end_time,location,instructor,notes,status FROM timetable_entries WHERE id=?",
        (entry_id,),
    )
    if not current.rows:
        raise ValueError("Timetable entry not found.")
    names = ["course","session_type","session_date","weekday","start_time","end_time","location","instructor","notes","status"]
    old = dict(zip(names, current.rows[0]))
    if changes.get("status", old["status"]) not in {"scheduled", "cancelled", "superseded"}:
        raise ValueError("Invalid status.")
    for field in ("session_date",):
        if field in changes and changes[field] and _parse_date(changes[field]) != changes[field]:
            raise ValueError(f"{field} must be YYYY-MM-DD or empty.")
    for field in ("start_time", "end_time"):
        if field in changes and changes[field] and _parse_time(changes[field]) != changes[field]:
            raise ValueError(f"{field} must be HH:MM or empty.")
    new = {**old, **changes}
    db.execute(
        """INSERT INTO timetable_changes(entry_id,changed_by,changed_at,old_values_json,new_values_json,reason)
           VALUES (?,?,?,?,?,?)""",
        (entry_id, actor_id, now, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), reason[:1000]),
    )
    assignments = ",".join(f"{field}=?" for field in changes)
    db.execute(
        f"UPDATE timetable_entries SET {assignments}, updated_at=? WHERE id=?",
        tuple(changes.values()) + (now, entry_id),
    )


def save_announcement(db: Any, *, chat_id: int, message_id: int, source_date: str, source_link: str,
                      original_text: str, category: str, relevance: str, draft_summary: str, now: str) -> None:
    initial_status = "pending_review" if relevance == "relevant" else "ignored"
    db.execute(
        """INSERT INTO announcements(source_chat_id,source_message_id,source_date,source_link,original_text,category,relevance,draft_summary,status,first_seen_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?, ?, ?, ?)
           ON CONFLICT(source_chat_id,source_message_id) DO UPDATE SET
             source_date=excluded.source_date, source_link=excluded.source_link, original_text=excluded.original_text,
             category=excluded.category, relevance=excluded.relevance, draft_summary=excluded.draft_summary,
             status=CASE WHEN excluded.relevance='relevant' THEN 'pending_review' ELSE 'ignored' END,
             updated_at=excluded.updated_at""",
        (chat_id, message_id, source_date, source_link, original_text[:20000], category[:100], relevance[:50], draft_summary[:5000], initial_status, now, now),
    )


def pending_announcements(db: Any, limit: int = 10) -> list[tuple]:
    return list(db.execute(
        """SELECT id,source_date,source_link,original_text,category,draft_summary,status
           FROM announcements WHERE status='pending_review' ORDER BY source_date DESC LIMIT ?""",
        (max(1, min(30, limit)),),
    ).rows)
