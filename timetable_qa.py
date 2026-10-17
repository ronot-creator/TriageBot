"""Deterministic first-year timetable intent resolution and safe Turso queries."""
from __future__ import annotations
import re, unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any

CAIRO=ZoneInfo("Africa/Cairo")
SCHEDULE_TERMS=("timetable","schedule","lecture","practical","section","gd","tbl","pg","room","hall","when is","what do we have","what is on","جدول","محاضرة","محاضرات","عملي","سكشن","سيكشن","قاعة","مدرج","امتحان","امتحانات","ميدترم","فاينل","امتى","متى","النهارده","انهارده","group discussion","group discussions","discussion group","مناقشة جماعية","مناقشات جماعية","جروب ديسكشن","ديسكشن")
EXAM_TERMS=("exam","exams","midterm","mid term","final","امتحان","امتحانات","ميدترم","فاينل")
ACTIVITY_CANONICAL={"lecture","practical","gd","tbl","pg"}

def normalize_text(value: Any)->str:
    text=unicodedata.normalize("NFKC",str(value or "")).lower().replace("ـ","")
    text=re.sub(r"[\u064B-\u065F\u0670]","",text)
    text=text.translate(str.maketrans({"أ":"ا","إ":"ا","آ":"ا","ى":"ي","٠":"0","١":"1","٢":"2","٣":"3","٤":"4","٥":"5","٦":"6","٧":"7","٨":"8","٩":"9"}))
    return re.sub(r"\s+"," ",text).strip()

def looks_like_schedule_question(text:str)->bool:
    n=normalize_text(text)
    for term in SCHEDULE_TERMS:
        candidate=normalize_text(term)
        if re.search(r"[a-z0-9]",candidate):
            if re.search(r"(?<![a-z0-9])"+re.escape(candidate)+r"(?![a-z0-9])",n):
                return True
        elif candidate in n:
            return True

    # Generic mentions of a group are common in ordinary student chat. Do not
    # route them to timetable Q&A unless there is a group identifier plus a
    # date/time or timetable cue.
    group_ref = re.search(r"(?:group|grp|جروب|مجموعة)\\s*#?\\s*\\d{1,2}\\b", n)
    if group_ref:
        contextual_cues = (
            "when", "where", "what", "tomorrow", "today", "next week", "next",
            "timetable", "schedule", "lecture", "practical", "exam", "gd", "tbl",
            "room", "hall", "امتى", "متى", "بكره", "بكرة", "بكرا", "النهارده",
            "جدول", "محاضرة", "عملي", "امتحان", "ديسكشن",
        )
        if any(normalize_text(cue) in n for cue in contextual_cues):
            return True
    return False

def _contains_alias(text:str,alias:str)->bool:
    alias=normalize_text(alias)
    if not alias:return False
    if re.search(r"[a-z0-9]",alias):
        return re.search(r"(?<![a-z0-9])"+re.escape(alias)+r"(?![a-z0-9])",text) is not None
    return alias in text

def load_aliases(db:Any)->list[dict[str,str]]:
    rows=db.execute("SELECT entity_type,canonical_value,alias,COALESCE(notes,'') FROM translation_aliases").rows
    return [{"entity_type":str(r[0]),"canonical_value":str(r[1]),"alias":str(r[2]),"notes":str(r[3] or "")} for r in rows]

def resolve_intent(text:str,aliases:list[dict[str,str]],today:date|None=None)->dict[str,Any]:
    n=normalize_text(text)
    intent={"exam":any(normalize_text(t) in n for t in EXAM_TERMS),"subject":None,"activity":None,"non_lecture":False,"room":None,"group":None,"date":None,"date_start":None,"date_end":None,"weekday":None,"next":False}
    today=today or datetime.now(CAIRO).date()
    from datetime import timedelta
    if any(t in n for t in ("day after tomorrow","بعد بكرة","بعد بكرا","بعد غد")):
        intent["date"]=(today+timedelta(days=2)).isoformat()
    elif any(t in n for t in ("today","النهارده","انهارده","النهاردة")):
        intent["date"]=today.isoformat()
    elif any(t in n for t in ("tomorrow","بكره","بكرة","بكرا")):
        intent["date"]=(today+timedelta(days=1)).isoformat()
    if any(t in n for t in ("next week","الأسبوع القادم","الاسبوع القادم","الأسبوع الجاي","الاسبوع الجاي","الاسبوع اللي جاي")):
        next_monday=today+timedelta(days=7-today.weekday())
        intent["date_start"]=next_monday.isoformat()
        intent["date_end"]=(next_monday+timedelta(days=6)).isoformat()
    intent["next"]=any(t in n for t in ("next","upcoming","الجاية","القادم","القادمة"))
    for item in sorted(aliases,key=lambda a:len(normalize_text(a["alias"])),reverse=True):
        if not _contains_alias(n,item["alias"]):continue
        kind=normalize_text(item["entity_type"]); canon=item["canonical_value"].strip(); cn=normalize_text(canon)
        if kind=="subject" and not intent["subject"]:intent["subject"]=canon
        elif kind=="room" and not intent["room"]:intent["room"]=canon
        elif kind=="activity type" and not intent["activity"]:
            if cn=="other / source label" or normalize_text(item["alias"])=="section":intent["non_lecture"]=True
            elif cn in ACTIVITY_CANONICAL:intent["activity"]=canon
    gm=re.search(r"(?:group|grp|جروب|مجموعة)\s*#?\s*(\d{1,2})\b",n)
    if gm:intent["group"]=int(gm.group(1))
    iso=re.search(r"\b20\d{2}-\d{2}-\d{2}\b",n)
    dm=re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b",n)
    if iso:
        try:intent["date"]=date.fromisoformat(iso.group(0)).isoformat()
        except ValueError:pass
    elif dm:
        d,m,y=map(int,dm.groups())
        try:intent["date"]=date(y,m,d).isoformat()
        except ValueError:pass
    weekday_map={"monday":"Monday","tuesday":"Tuesday","wednesday":"Wednesday","thursday":"Thursday","friday":"Friday","saturday":"Saturday","sunday":"Sunday","الاحد":"Sunday","الاثنين":"Monday","الثلاثاء":"Tuesday","الاربعاء":"Wednesday","الخميس":"Thursday","الجمعة":"Friday","السبت":"Saturday"}
    for alias,canonical in weekday_map.items():
        if normalize_text(alias) in n:intent["weekday"]=canonical;break
    return intent

def _groups_from_text(value:Any)->set[int]:
    normalized=normalize_text(value)
    groups={int(x) for x in re.findall(r"(?<!\d)(\d{1,2})(?!\d)",normalized)}
    word_numbers={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"واحد":1,"واحدة":1,"اتنين":2,"اثنين":2,"تلاتة":3,"ثلاثة":3,"اربعة":4,"خمسة":5,"ستة":6,"سبعة":7,"تمانية":8,"ثمانية":8,"تسعة":9,"عشرة":10}
    for word,number in word_numbers.items():
        if re.search(r"(?<![a-z0-9])"+re.escape(normalize_text(word))+r"(?![a-z0-9])",normalized):
            groups.add(number)
    return groups

def _time(value:Any)->str:
    raw=str(value or "").strip();return raw[:5] if raw else "time not specified"

def answer_timetable_question(db:Any,text:str,current_term:str="term1")->str|None:
    if not looks_like_schedule_question(text):return None
    try:
        aliases=load_aliases(db); intent=resolve_intent(text,aliases)
        normalized=normalize_text(text)
        asks_for_second_term = any(term in normalized for term in ("second semester", "second term", "term 2", "term2", "الفصل الثاني", "الترم الثاني", "الترم التاني"))
        if asks_for_second_term:
            return "المتاح عندي حالياً بيانات الفصل الأول فقط (term1)، لذلك لا أقدر أؤكد مواعيد الفصل الثاني من غير بياناته."
        if current_term != "term1":
            return "قاعدة البيانات الحالية تحتوي على بيانات الفصل الأول فقط (term1)."
        count_intent=bool(re.search(r"\b(how many|count|number of|كم|كام|عدد)\b",normalized))
        gd_terms=("group discussion","group discussions","discussion group","gd","مناقشة جماعية","مناقشات جماعية","جروب ديسكشن","ديسكشن")
        is_gd_count=count_intent and any(term in normalized for term in gd_terms)
        if is_gd_count:
            group=intent["group"]
            if group is None:
                word_group=re.search(r"(?:group|grp|جروب|مجموعة)\s+(one|two|three|four|five|six|seven|eight|nine|ten|واحد|واحدة|اتنين|اثنين|تلاتة|ثلاثة|اربعة|خمسة|ستة|سبعة|تمانية|ثمانية|تسعة|عشرة)\b",normalized)
                words={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"واحد":1,"واحدة":1,"اتنين":2,"اثنين":2,"تلاتة":3,"ثلاثة":3,"اربعة":4,"خمسة":5,"ستة":6,"سبعة":7,"تمانية":8,"ثمانية":8,"تسعة":9,"عشرة":10}
                if word_group: group=words.get(word_group.group(1))
            if group is None:
                return "أقدر أحسبها من الجدول، لكن أحتاج رقم المجموعة أولاً. تقصد أي مجموعة؟"
            sql="SELECT activity_type,groups_text,event_date FROM timetable_events WHERE year_level=?"
            args=["First year"]
            if intent["date_start"] and intent["date_end"]:
                sql+=" AND event_date>=? AND event_date<=?"
                args.extend((intent["date_start"],intent["date_end"]))
            if intent["date"]:sql+=" AND event_date=?";args.append(intent["date"])
            rows=db.execute(sql,tuple(args)).rows
            matching=[]
            for row in rows:
                activity=normalize_text(row[0])
                if any(term in activity for term in gd_terms) and group in _groups_from_text(row[1]):
                    matching.append(row)
            if intent["date_start"] and intent["date_end"]:
                period=f"خلال الفترة من {intent['date_start']} إلى {intent['date_end']}"
            elif intent["date"]:
                period=f"في تاريخ {intent['date']}"
            else:
                period="ضمن بيانات الفصل الأول الموجودة في ملف السنة الأولى المستورد"
            return f"حسب الجدول المستورد، للمجموعة {group} عدد {len(matching)} من جلسات Group Discussion (GD) {period}."
        # TBL status is maintained by leaders; never infer it from a GD entry.
        # Missing records are unknown, not proof that a TBL exists or does not exist.
        asks_tbl = bool(re.search(r"(?<![a-z0-9])tbl(?![a-z0-9])", normalized)) or any(
            term in normalized for term in ("team based learning", "team-based learning", "تعلم جماعي", "تي بي ال")
        )
        if asks_tbl:
            group = intent["group"]
            if group is None:
                group_match = re.search(
                    r"(?:group|grp|جروب|مجموعة)\s+(one|two|three|four|five|six|seven|eight|nine|ten|واحد|واحدة|اتنين|اثنين|تلاتة|ثلاثة|اربعة|خمسة|ستة|سبعة|تمانية|ثمانية|تسعة|عشرة)\b",
                    normalized,
                )
                words = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,
                         "واحد":1,"واحدة":1,"اتنين":2,"اثنين":2,"تلاتة":3,"ثلاثة":3,"اربعة":4,"خمسة":5,"ستة":6,"سبعة":7,"تمانية":8,"ثمانية":8,"تسعة":9,"عشرة":10}
                if group_match:
                    group = words.get(group_match.group(1))
            if group is None:
                return "أقدر أراجع حالة الـTBL، لكن أحتاج رقم المجموعة أولاً. تقصد أي مجموعة؟"
            change_rows = db.execute(
                """SELECT activity_key,status,effective_start,effective_end,details,related_activity,created_at
                   FROM activity_changes
                   WHERE active=1 AND upper(activity_type)='TBL'
                   ORDER BY id DESC LIMIT 200"""
            ).rows
            matching_changes = []
            for row in change_rows:
                key, status, start, end, details, related, created = row
                searchable = f"{key or ''} {details or ''} {related or ''}"
                if group in _groups_from_text(searchable):
                    if intent["date"]:
                        if start and intent["date"] < str(start): continue
                        if end and intent["date"] > str(end): continue
                    matching_changes.append(row)
            if not matching_changes:
                # The imported workbook is the baseline source of scheduled activities.
                # Leadership changes override it, but absence of a change is not absence
                # of a scheduled TBL.
                base_sql = """SELECT activity_type,groups_text,event_date,day,source_text
                              FROM timetable_events
                              WHERE year_level=?"""
                base_args: list[Any] = ["First year"]
                if intent["date_start"] and intent["date_end"]:
                    base_sql += " AND event_date>=? AND event_date<=?"
                    base_args.extend((intent["date_start"], intent["date_end"]))
                if intent["date"]:
                    base_sql += " AND event_date=?"
                    base_args.append(intent["date"])
                base_sql += " ORDER BY event_date"
                base_rows = db.execute(base_sql, tuple(base_args)).rows
                scheduled = []
                for base_row in base_rows:
                    activity, groups_text, event_date, day, source_text = base_row
                    activity_norm = normalize_text(activity)
                    is_tbl_row = (
                        re.search(r"(?<![a-z0-9])tbl(?![a-z0-9])", activity_norm) is not None
                        or "team based learning" in activity_norm
                        or "team-based learning" in activity_norm
                    )
                    if is_tbl_row and group in _groups_from_text(groups_text):
                        scheduled.append(base_row)
                if scheduled:
                    lines = [f"حسب الجدول المستورد، يوجد TBL مدرج للمجموعة {group}."]
                    for activity, groups_text, event_date, day, source_text in scheduled[:10]:
                        lines.append(f"• {event_date or 'التاريخ غير محدد'} ({day or 'اليوم غير محدد'}) — {activity}")
                    lines.append("لم أجد تحديثاً أحدث من القيادة يغيّر هذه البيانات.")
                    return "\n".join(lines)[:1800]
                return f"لا توجد لديّ معلومة مؤكدة عن وجود أو عدم وجود TBL للمجموعة {group} في البيانات الحالية. لم يظهر نشاط مطابق في الجدول المستورد ولم يتم تسجيل تحديث من القيادة."
            latest = matching_changes[0]
            _, status, start, end, details, related, created = latest
            status = str(status or "unknown").lower()
            if status in {"confirmed", "added"}:
                label = "مؤكد"
            elif status == "cancelled":
                label = "ملغي حسب آخر تحديث مسجل"
            elif status == "removed":
                label = "غير موجود حسب آخر تحديث مسجل"
            elif status == "rescheduled":
                label = "تم تغيير موعده حسب آخر تحديث مسجل"
            else:
                label = "غير مؤكد حتى الآن"
            lines = [f"حالة TBL للمجموعة {group}: {label}."]
            if start or end:
                lines.append("الفترة المسجلة: " + str(start or "بداية غير محددة") + " إلى " + str(end or "نهاية غير محددة") + ".")
            if details:
                lines.append("التفاصيل: " + str(details))
            lines.append("المصدر: تحديث محفوظ من القيادة؛ راجع القيادة إذا كنت تحتاج تأكيداً إضافياً.")
            return "\n".join(lines)[:1800]
        if intent["exam"]:
            sql="SELECT event_date,day,assessment_type,subject,activity_component,source_text,review_note FROM exam_events WHERE year_level=?"
            args:list[Any]=["First year"]
            if intent["date_start"] and intent["date_end"]:
                sql+=" AND event_date>=? AND event_date<=?"
                args.extend((intent["date_start"],intent["date_end"]))
            if intent["date"]:sql+=" AND event_date=?";args.append(intent["date"])
            if intent["subject"]:sql+=" AND lower(subject)=lower(?)";args.append(intent["subject"])
            if intent["next"]:sql+=" AND event_date>=?";args.append(datetime.now(CAIRO).date().isoformat())
            sql+=" ORDER BY event_date LIMIT 30"; rows=db.execute(sql,tuple(args)).rows
            if not rows:return "لم أجد امتحاناً مطابقاً في جدول السنة الأولى المستورد. هذا لا يثبت عدم وجود امتحان؛ قد تكون البيانات ناقصة."
            lines=["امتحانات السنة الأولى حسب الجدول المستورد:"]
            for r in rows:
                comp=f" ({r[4]})" if r[4] else ""; note=f" — ملاحظة: {r[6]}" if r[6] else ""
                lines.append(f"• {r[0]} ({r[1]}) — {r[2]}: {r[3]}{comp}{note}")
            return "\n".join(lines)[:3800]
        sql="""SELECT event_date,day,start_time,end_time,subject,activity_type,groups_text,room_text,group_room_association,review_note,source_page FROM timetable_events WHERE year_level=?"""
        args=["First year"]
        if intent["date_start"] and intent["date_end"]:
            sql+=" AND event_date>=? AND event_date<=?"
            args.extend((intent["date_start"],intent["date_end"]))
        if intent["date"]:sql+=" AND event_date=?";args.append(intent["date"])
        if intent["weekday"]:sql+=" AND day=?";args.append(intent["weekday"])
        if intent["subject"]:sql+=" AND lower(subject)=lower(?)";args.append(intent["subject"])
        if intent["room"]:sql+=" AND lower(room_text) LIKE lower(?)";args.append("%"+intent["room"]+"%")
        if intent["activity"]:sql+=" AND lower(activity_type)=lower(?)";args.append(intent["activity"])
        elif intent["non_lecture"]:sql+=" AND lower(activity_type)<>lower(?)";args.append("Lecture")
        if intent["next"]:sql+=" AND event_date>=?";args.append(datetime.now(CAIRO).date().isoformat())
        sql+=" ORDER BY event_date,start_time LIMIT 100"; rows=db.execute(sql,tuple(args)).rows
        if intent["group"] is not None:rows=[r for r in rows if intent["group"] in _groups_from_text(r[6])]
        if not rows:return "لم أجد نشاطاً مطابقاً في جدول السنة الأولى المستورد. قد تكون البيانات ناقصة أو تحتاج صياغة السؤال بشكل أوضح."
        if not any((intent["subject"],intent["activity"],intent["non_lecture"],intent["room"],intent["group"] is not None,intent["date"],intent["date_start"],intent["weekday"],intent["next"])):rows=rows[:10]
        lines=["الجدول المستورد للسنة الأولى:"]
        for r in rows[:20]:
            extra=[]
            if r[8]:extra.append(str(r[8]))
            if r[9]:extra.append("ملاحظة: "+str(r[9]))
            if r[10]:extra.append("مصدر: صفحة "+str(r[10]))
            suffix=" — "+"؛ ".join(extra) if extra else ""
            lines.append(f"• {r[0]} ({r[1]}) {_time(r[2])}–{_time(r[3])} | {r[4]} — {r[5]} | المجموعات: {r[6] or 'غير محددة'} | القاعة: {r[7] or 'المكان غير محدد'}{suffix}")
        if len(rows)>20:lines.append(f"تم عرض 20 نتيجة من أصل {len(rows)} نتيجة مطابقة.")
        return "\n".join(lines)[:3900]
    except Exception:
        return "خدمة الجدول غير جاهزة حالياً أو لم تُستورد بياناتها بعد. لم أستخدم تخميناً؛ يرجى المحاولة لاحقاً أو التواصل مع القيادة."
