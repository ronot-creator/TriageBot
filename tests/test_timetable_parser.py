import unittest

from college_data import parse_timetable


class TimetableParserTests(unittest.TestCase):
    def test_csv_extracts_course_date_times_and_location(self):
        payload = (
            "Course,Date,Start,End,Location,Instructor,Type\n"
            "Anatomy,2026-10-14,09:30,11:00,Hall 2,Dr Example,Lecture\n"
        ).encode("utf-8")
        result = parse_timetable("week.csv", payload)
        self.assertEqual(len(result["entries"]), 1)
        row = result["entries"][0]
        self.assertEqual(row["course"], "Anatomy")
        self.assertEqual(row["session_date"], "2026-10-14")
        self.assertEqual(row["start_time"], "09:30")
        self.assertEqual(row["end_time"], "11:00")
        self.assertEqual(row["location"], "Hall 2")
        self.assertEqual(row["instructor"], "Dr Example")
        self.assertEqual(row["session_type"], "Lecture")
        self.assertEqual(result["errors"], [])

    def test_weekday_and_time_range_are_parsed(self):
        payload = (
            "Subject,Day,Time,Place\n"
            "Physiology,Monday,10:00 - 11:30,Lab 1\n"
        ).encode("utf-8")
        result = parse_timetable("week.csv", payload)
        self.assertEqual(len(result["entries"]), 1)
        row = result["entries"][0]
        self.assertEqual(row["weekday"], "Monday")
        self.assertEqual(row["start_time"], "10:00")
        self.assertEqual(row["end_time"], "11:30")
        self.assertEqual(row["location"], "Lab 1")

    def test_unreadable_rows_are_reported_not_silently_imported(self):
        payload = (
            "Course,Date,Start,End,Location\n"
            "Anatomy,not-a-date,banana,11:00,Hall 2\n"
        ).encode("utf-8")
        result = parse_timetable("week.csv", payload)
        self.assertEqual(result["entries"], [])
        self.assertTrue(result["errors"])
        self.assertIn("date", result["errors"][0]["reason"])

    def test_missing_header_fails_closed(self):
        payload = b"hello,world\nfoo,bar\n"
        with self.assertRaises(ValueError):
            parse_timetable("week.csv", payload)

    def test_duplicate_empty_file_is_not_accepted_as_timetable(self):
        with self.assertRaises(ValueError):
            parse_timetable("empty.csv", b"")


if __name__ == "__main__":
    unittest.main()
