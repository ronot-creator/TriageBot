"""Deterministic first-year timetable intent resolution and safe Turso queries."""
from __future__ import annotations
import re, unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any

CAIRO=ZoneInfo("Africa/Cairo")
SCHEDULE_TERMS=("timetable","schedule","lecture","practical","section","gd","tbl","pg","group","room","hall","when is","what do we have","what is on","جدول","محاضرة","محاضرات","عملي","سكشن","سيكشن","جروب","مجموعة","قاعة","مدرج","امتحان","امتحانات","ميدترم","فاينل","امتى","متى","النهارده","انهارده","group discussion","group discussions","discussion group","group discussions","مناقشة جماعية","مناقشات جماعية","جروب ديسكشن","ديسكشن")
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
    intent={"exam":any(normalize_text(t) in n for t in EXAM_TERMS),"subject":None,"activity":None,"non_lecture":False,"room":None,"group":None,"date":None,"weekday":None,"next":False}
    today=today or datetime.now(CAIRO).date()
    if any(t in n for t in ("today","النهارده","انهارده")):
        intent["date"]=today.isoformat()
    elif any(t in n for t in ("tomorrow","بكره","بكرة")):
        from datetime import timedelta
        intent["date"]=(today+timedelta(days=1)).isoformat()
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

def answer_timetable_question(db:Any,text:str,term_start:str="",term_end:str="")->str|None:
    if not looks_like_schedule_question(text):return None
    try:
        aliases=load_aliases(db); intent=resolve_intent(text,aliases)
        normalized=normalize_text(text)
        term_bounds = None
        if term_start and term_end:
            try:
                parsed_start = date.fromisoformat(term_start)
                parsed_end = date.fromisoformat(term_end)
                if parsed_end >= parsed_start:
                    term_bounds = (parsed_start.isoformat(), parsed_end.isoformat())
            except ValueError:
                term_bounds = None
        asks_for_term = any(term in normalized for term in ("this semester", "this term", "current semester", "current term", "الفصل الحالي", "الترم الحالي", "الترم ده", "الفصل ده"))
        if asks_for_term and term_bounds is None:
            return "حدود الفصل الدراسي الحالي مش متسجلة بشكل موثوق. خلي مسؤول البوت يضبطها أولاً، عشان ما أخلطش مواعيد فصول مختلفة."
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
            if term_bounds:
                sql+=" AND event_date>=? AND event_date<=?"
                args.extend(term_bounds)
            rows=db.execute(sql,tuple(args)).rows
            matching=[]
            for row in rows:
                activity=normalize_text(row[0])
                if any(term in activity for term in gd_terms) and group in _groups_from_text(row[1]):
                    matching.append(row)
            return f"حسب جدول السنة الأولى المستورد، للمجموعة {group} عدد {len(matching)} من جلسات Group Discussion (GD). هذا العدد يشمل كل التواريخ الموجودة في الملف المستورد، وليس فصلاً دراسياً محدداً."
        if intent["exam"]:
            sql="SELECT event_date,day,assessment_type,subject,activity_component,source_text,review_note FROM exam_events WHERE year_level=?"
            args:list[Any]=["First year"]
            if term_bounds:
                sql+=" AND event_date>=? AND event_date<=?"
                args.extend(term_bounds)
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
        if term_bounds:
            sql+=" AND event_date>=? AND event_date<=?"
            args.extend(term_bounds)
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
        if not any((intent["subject"],intent["activity"],intent["non_lecture"],intent["room"],intent["group"] is not None,intent["date"],intent["weekday"],intent["next"])):rows=rows[:10]
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
