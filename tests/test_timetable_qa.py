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

class FakeDB:
 def execute(self,sql,args=()):
  class Result:
   def __init__(self,rows):self.rows=rows
  if "FROM translation_aliases" in sql:return Result([])
  if "SELECT activity_type,groups_text,event_date FROM timetable_events" in sql:
   return Result([
    ("Group Discussion","1, 2, 6","2026-10-01"),
    ("GD","Group 6, Group 7","2026-10-03"),
    ("Lecture","6","2026-10-04"),
    ("Group Discussion","14","2026-10-05"),
   ])
  raise AssertionError("Unexpected query: "+sql)
class TimetableCountTests(unittest.TestCase):
 def test_group_six_count_is_deterministic(self):
  answer=answer_timetable_question(FakeDB(),"How many group discussions does group six have?")
  self.assertIn("عدد 2",answer)
 def test_arabic_group_number_count(self):
  answer=answer_timetable_question(FakeDB(),"عدد الـ GD للمجموعة ٦ كام؟")
  self.assertIn("عدد 2",answer)
 def test_missing_group_asks_for_clarification(self):
  answer=answer_timetable_question(FakeDB(),"How many group discussions do we have?")
  self.assertIn("أي مجموعة",answer)
\nif __name__=="__main__":unittest.main()
