import unittest
from datetime import date
from timetable_qa import normalize_text, looks_like_schedule_question, resolve_intent, _groups_from_text, answer_timetable_question
ALIASES=[
 {"entity_type":"Subject","canonical_value":"Anatomy","alias":"Anatomy","notes":""},
 {"entity_type":"Subject","canonical_value":"Physiology","alias":"Physiology","notes":""},
 {"entity_type":"Activity Type","canonical_value":"Practical","alias":"Practical","notes":""},
 {"entity_type":"Activity Type","canonical_value":"GD","alias":"GD","notes":""},
 {"entity_type":"Activity Type","canonical_value":"Other / source label","alias":"Section","notes":""},
 {"entity_type":"Room","canonical_value":"Hall 8","alias":"مدرج 8","notes":""},
]
class TimetableIntentTests(unittest.TestCase):
 def test_arabic_digits(self):self.assertEqual(normalize_text("جروب ٤"),"جروب 4")
 def test_group_exactness(self):
  self.assertEqual(_groups_from_text("4, 14"),{4,14});self.assertNotIn(4,_groups_from_text("14"))
 def test_subject_activity_group(self):
  i=resolve_intent("When is Anatomy Practical for Group 4?",ALIASES,date(2026,10,16))
  self.assertEqual((i["subject"],i["activity"],i["group"]),("Anatomy","Practical",4))
 def test_section_is_broad(self):
  i=resolve_intent("When is the section?",ALIASES,date(2026,10,16));self.assertTrue(i["non_lecture"]);self.assertIsNone(i["activity"])
 def test_exam_arabic(self):self.assertTrue(resolve_intent("امتى الامتحان",ALIASES,date(2026,10,16))["exam"])
 def test_schedule_detection_boundaries(self):
  self.assertTrue(looks_like_schedule_question("متى امتحان Anatomy؟"))
  self.assertFalse(looks_like_schedule_question("I am upset today"))
  self.assertFalse(looks_like_schedule_question("What shall we do next?"))
 def test_invalid_date(self):self.assertIsNone(resolve_intent("Anatomy 31/02/2027",ALIASES,date(2026,10,16))["date"])
 def test_arabic_room_alias(self):self.assertEqual(resolve_intent("Where is مدرج 8?",ALIASES,date(2026,10,16))["room"],"Hall 8")
 def test_tomorrow_is_resolved_by_code(self):self.assertEqual(resolve_intent("What do we have tomorrow?",ALIASES,date(2026,10,16))["date"],"2026-10-17")
class CurrentTermTests(unittest.TestCase):
 def test_second_term_refuses_to_guess_when_only_term_one_data_exists(self):
  class EmptyDB:
   def execute(self,sql,args=()):
    class Result:
     rows=[]
    return Result()
  answer=answer_timetable_question(EmptyDB(),"When is the GD in the second term?")
  self.assertIn("بيانات الفصل الأول فقط",answer)
 def test_gd_count_uses_imported_term_one_dataset(self):
  class RecordingDB:
   def __init__(self):self.query=None
   def execute(self,sql,args=()):
    class Result:
     def __init__(self,rows):self.rows=rows
    if "FROM translation_aliases" in sql:return Result([])
    if "SELECT activity_type,groups_text,event_date FROM timetable_events" in sql:
     self.query=(sql,args)
     return Result([("GD","Group 6","2026-10-10"),("GD","Group 6","2026-11-10")])
    raise AssertionError("Unexpected query: "+sql)
  db=RecordingDB()
  answer=answer_timetable_question(db,"How many GDs does group 6 have this term?","term1")
  self.assertIn("عدد 2",answer)
  self.assertEqual(db.query[1],("First year",))
 def test_next_week_resolves_to_local_calendar_week(self):
  intent=resolve_intent("What GDs are there next week?",ALIASES,date(2026,10,16))
  self.assertEqual((intent["date_start"],intent["date_end"]),("2026-10-19","2026-10-25"))
 def test_day_after_tomorrow(self):
  intent=resolve_intent("When is the GD the day after tomorrow?",ALIASES,date(2026,10,16))
  self.assertEqual(intent["date"],"2026-10-18")

class TBLStatusOverlayTests(unittest.TestCase):
 def _db(self,changes):
  class Result:
   def __init__(self,rows):self.rows=rows
  class DB:
   def execute(self,sql,args=()):
    if "FROM translation_aliases" in sql:return Result([])
    if "FROM activity_changes" in sql:return Result(changes)
    if "FROM timetable_events" in sql:return Result([])
    raise AssertionError("Unexpected query: "+sql)
  return DB()
 def test_missing_tbl_record_is_unknown_not_absent(self):
  answer=answer_timetable_question(self._db([]),"Is there a TBL for group 6?")
  self.assertIn("لم يظهر نشاط مطابق في الجدول المستورد",answer)
  self.assertIn("لم يتم تسجيل تحديث من القيادة",answer)
 def test_imported_tbl_baseline_is_used_without_leader_override(self):
  class Result:
   def __init__(self,rows):self.rows=rows
  class DB:
   def execute(self,sql,args=()):
    if "FROM translation_aliases" in sql:return Result([])
    if "FROM activity_changes" in sql:return Result([])
    if "FROM timetable_events" in sql:return Result([("TBL","Group 6","2026-10-20","Tuesday","Imported workbook")])
    raise AssertionError("Unexpected query: "+sql)
  answer=answer_timetable_question(DB(),"Is there a TBL for group 6?")
  self.assertIn("يوجد TBL مدرج للمجموعة 6",answer)
  self.assertIn("2026-10-20",answer)

 def test_confirmed_tbl_uses_leader_record(self):
  answer=answer_timetable_question(self._db([
   ("TBL group 6","confirmed",None,None,"Confirmed by leaders","GD","2026-10-16")
  ]),"Is there a TBL for group 6?")
  self.assertIn("مؤكد",answer)
  self.assertIn("Confirmed by leaders",answer)
 def test_possible_tbl_stays_unconfirmed(self):
  answer=answer_timetable_question(self._db([
   ("TBL group 6","unknown",None,None,"Possible, not confirmed","GD","2026-10-16")
  ]),"Is there a TBL for group 6?")
  self.assertIn("غير مؤكد",answer)

if __name__=="__main__":unittest.main()
