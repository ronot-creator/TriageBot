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
class TermBoundaryTests(unittest.TestCase):
 def test_explicit_term_without_config_asks_for_reliable_boundaries(self):
  class EmptyDB:
   def execute(self,sql,args=()):
    class Result:
     rows=[]
    return Result()
  answer=answer_timetable_question(EmptyDB(),"How many GDs do we have this semester?")
  self.assertIn("حدود الفصل الدراسي الحالي",answer)

 def test_term_dates_are_applied_to_gd_count_query(self):
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
  answer=answer_timetable_question(db,"How many GDs does group 6 have this term?","2026-10-01","2026-10-31")
  self.assertIn("عدد 1",answer)
  self.assertIn("event_date>=? AND event_date<=?",db.query[0])
  self.assertEqual(db.query[1],("First year","2026-10-01","2026-10-31"))

if __name__=="__main__":unittest.main()
