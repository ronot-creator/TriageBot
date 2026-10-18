"""Regression tests for the bot's deterministic persistence and quota guards.

These tests mock the external database/client constructors: they never contact
Turso, Telegram, or Gemini and consume no API quota.
"""
from __future__ import annotations

import importlib
import os
import sys
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class FakeResult:
    def __init__(self, rows=None, rows_affected=0):
        self.rows = rows or []
        self.rows_affected = rows_affected


class FakeDB:
    def __init__(self):
        self.calls = []
        self.blacklisted = set()
        self.config = {}
        self.model_usage = {}
        self.api_usage = {}
        self.processed = set()

    def execute(self, sql, args=()):
        args = tuple(args)
        self.calls.append((sql, args))
        normalized = " ".join(sql.split()).lower()

        if normalized.startswith("select 1 from blacklist"):
            return FakeResult([(1,)] if args[0] in self.blacklisted else [])
        if normalized.startswith("select value from config"):
            return FakeResult([(self.config[args[0]],)] if args[0] in self.config else [])
        if normalized.startswith("insert into config"):
            self.config[args[0]] = args[1]
            return FakeResult(rows_affected=1)
        if normalized.startswith("insert or ignore into model_usage"):
            self.model_usage.setdefault((args[0], args[1]), 0)
            return FakeResult(rows_affected=1)
        if normalized.startswith("update model_usage set requests=requests+1"):
            key = (args[0], args[1])
            if self.model_usage.get(key, 0) < args[2]:
                self.model_usage[key] = self.model_usage.get(key, 0) + 1
                return FakeResult(rows_affected=1)
            return FakeResult(rows_affected=0)
        if normalized.startswith("insert or ignore into api_usage"):
            self.api_usage.setdefault(args[0], 0)
            return FakeResult(rows_affected=1)
        if normalized.startswith("update api_usage set requests=requests+1"):
            self.api_usage[args[0]] = self.api_usage.get(args[0], 0) + 1
            return FakeResult(rows_affected=1)
        if normalized.startswith("insert or ignore into processed_messages"):
            key = (args[0], args[1])
            if key in self.processed:
                return FakeResult(rows_affected=0)
            self.processed.add(key)
            return FakeResult(rows_affected=1)
        return FakeResult()


def load_bot_with_mocked_clients():
    fake_db = FakeDB()
    env = {
        "TELEGRAM_BOT_TOKEN": "test-telegram-token",
        "GEMINI_API_KEY": "test-gemini-key",
        "TURSO_DATABASE_URL": "https://example.turso.io",
        "TURSO_AUTH_TOKEN": "test-turso-token",
        "OWNER_TELEGRAM_ID": "999",
        "GEMINI_PER_MODEL_RPM_LIMIT": "15",
        "GEMINI_PER_MODEL_RPD_LIMIT": "2",
        "ALLOW_LOCAL_DB": "0",
    }
    # Import bot.py only with all network-facing constructors mocked.
    with patch.dict(os.environ, env):
        with patch("libsql_client.create_client_sync", return_value=fake_db):
            with patch("google.genai.Client") as gemini_client:
                gemini_client.return_value.aio = SimpleNamespace()
                if "bot" in sys.modules:
                    module = importlib.reload(sys.modules["bot"])
                else:
                    module = importlib.import_module("bot")
    return module, fake_db


BOT, FAKE_DB = load_bot_with_mocked_clients()


class BotPersistenceAndQuotaTests(unittest.TestCase):
    def setUp(self):
        FAKE_DB.calls.clear()
        FAKE_DB.blacklisted.clear()
        FAKE_DB.config.clear()
        FAKE_DB.model_usage.clear()
        FAKE_DB.api_usage.clear()
        FAKE_DB.processed.clear()
        BOT.MODEL_NAME = "gemini-3.5-flash-lite"
        BOT.FALLBACK_MODEL = "gemini-3.1-flash-lite"
        BOT.RPD_LIMIT = 2

    def test_only_two_approved_models_are_allowed(self):
        self.assertEqual(
            BOT.APPROVED_MODELS,
            {"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"},
        )
        self.assertFalse(BOT.reserve_daily_request("unapproved-model"))
        self.assertEqual(FAKE_DB.model_usage, {})

    def test_daily_quota_is_enforced_per_model(self):
        primary = "gemini-3.5-flash-lite"
        fallback = "gemini-3.1-flash-lite"
        self.assertTrue(BOT.reserve_daily_request(primary))
        self.assertTrue(BOT.reserve_daily_request(primary))
        self.assertFalse(BOT.reserve_daily_request(primary))
        self.assertTrue(BOT.reserve_daily_request(fallback))
        self.assertTrue(BOT.reserve_daily_request(fallback))
        self.assertFalse(BOT.reserve_daily_request(fallback))
        self.assertEqual(sorted(FAKE_DB.model_usage.values()), [2, 2])

    def test_message_deduplication_allows_only_first_claim(self):
        self.assertTrue(BOT.claim_message_for_processing(-100123, 77))
        self.assertFalse(BOT.claim_message_for_processing(-100123, 77))
        self.assertTrue(BOT.claim_message_for_processing(-100123, 78))

    def test_owner_cannot_be_blacklisted(self):
        FAKE_DB.blacklisted.add(999)
        self.assertFalse(BOT.is_blacklisted(999))

    def test_non_owner_blacklist_is_respected(self):
        FAKE_DB.blacklisted.add(123)
        self.assertTrue(BOT.is_blacklisted(123))
        self.assertFalse(BOT.is_blacklisted(124))

    def test_group_context_persistence_keeps_chat_and_user_identity(self):
        message = SimpleNamespace(
            chat_id=-100123,
            message_id=55,
            from_user=SimpleNamespace(is_bot=False, id=321, full_name="Student", username=None),
            text="When is anatomy?",
            caption=None,
            photo=None,
            video=None,
            voice=None,
            audio=None,
            document=None,
            animation=None,
            sticker=None,
        )
        BOT.persist_context_message(message)
        insert = next(
            (sql, args) for sql, args in FAKE_DB.calls
            if "INSERT OR IGNORE INTO conversation_messages" in sql
        )
        self.assertEqual(insert[1][:3], (-100123, 55, 321))
        self.assertIn("'user'", insert[0])

    def test_assistant_context_is_associated_with_the_originating_user(self):
        BOT.persist_assistant_message(-100123, 56, "Tuesday, 9 AM", user_id=321)
        insert = next(
            (sql, args) for sql, args in FAKE_DB.calls
            if "INSERT OR IGNORE INTO conversation_messages" in sql
        )
        self.assertEqual(insert[1][:3], (-100123, 56, 321))
        self.assertIn("'assistant'", insert[0])


class ActivityUpdateDetectionTests(unittest.TestCase):
    def test_detects_explicit_english_activity_updates_without_ai(self):
        self.assertTrue(BOT.looks_like_activity_update("TBL group 6 is cancelled tomorrow"))
        self.assertTrue(BOT.looks_like_activity_update("The lecture moved to Thursday at 10"))
        self.assertTrue(BOT.looks_like_activity_update("GD is confirmed for group 3"))

    def test_detects_arabic_activity_updates_without_ai(self):
        self.assertTrue(BOT.looks_like_activity_update("الـTBL اتلغى بكرة"))
        self.assertTrue(BOT.looks_like_activity_update("محاضرة التشريح اتأجلت ليوم الخميس"))

    def test_ignores_ordinary_chatter_and_unrelated_keywords(self):
        self.assertFalse(BOT.looks_like_activity_update("Good morning everyone"))
        self.assertFalse(BOT.looks_like_activity_update("I like the lecture"))
        self.assertFalse(BOT.looks_like_activity_update("TBL was difficult today"))

    def test_detector_only_identifies_candidates_not_authority(self):
        # Authority is enforced separately by leader_clarification_handler/is_leader.
        self.assertTrue(BOT.looks_like_activity_update("TBL cancelled tomorrow"))


class LeaderGroupConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FAKE_DB.config.clear()
        BOT.OWNER_ID = 999

    async def test_only_owner_can_set_leader_group(self):
        replies = []

        async def reply_text(text):
            replies.append(text)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(type="supergroup", id=-100555),
            effective_message=SimpleNamespace(reply_text=reply_text),
        )
        await BOT.setleaders_command(update, SimpleNamespace())
        self.assertEqual(FAKE_DB.config.get("leader_chat_id"), None)
        self.assertTrue(any("مالك البوت فقط" in text for text in replies))

    async def test_owner_must_run_setleaders_inside_a_group(self):
        replies = []

        async def reply_text(text):
            replies.append(text)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=999),
            effective_chat=SimpleNamespace(type="private", id=999),
            effective_message=SimpleNamespace(reply_text=reply_text),
        )
        await BOT.setleaders_command(update, SimpleNamespace())
        self.assertEqual(FAKE_DB.config.get("leader_chat_id"), None)
        self.assertTrue(any("داخل جروب القيادة" in text for text in replies))

    async def test_owner_sets_leader_group_id_in_persistent_config(self):
        replies = []

        async def reply_text(text):
            replies.append(text)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=999),
            effective_chat=SimpleNamespace(type="supergroup", id=-100555),
            effective_message=SimpleNamespace(reply_text=reply_text),
        )
        await BOT.setleaders_command(update, SimpleNamespace())
        self.assertEqual(FAKE_DB.config.get("leader_chat_id"), "-100555")
        self.assertTrue(any("-100555" in text for text in replies))

    async def test_leader_cannot_blacklist_owner_from_ticket(self):
        answers = []

        async def answer(text, show_alert=False):
            answers.append((text, show_alert))

        query = SimpleNamespace(answer=answer)
        update = SimpleNamespace(callback_query=query)
        with patch.object(BOT, "ticket_row", return_value=(1, "URGENT", "summary", 999)):
            await BOT.blacklist_ticket_callback(update, SimpleNamespace(), 42, 123)
        self.assertFalse(any("INSERT INTO blacklist" in sql for sql, _ in FAKE_DB.calls))
        self.assertTrue(any("مالك البوت" in text for text, _ in answers))


class PrivateQuestionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_private_message_gets_direct_ai_answer_and_is_persisted(self):
        replies = []

        async def reply_text(text, **kwargs):
            replies.append(text)
            return SimpleNamespace(message_id=89)

        user = SimpleNamespace(id=321, full_name="Student", username=None, is_bot=False)
        message = SimpleNamespace(
            chat_id=321, message_id=88, from_user=user, text="Is TBL cancelled?", caption=None,
            reply_to_message=None, photo=None, video=None, voice=None, audio=None, document=None,
            animation=None, sticker=None,
        )
        message.reply_text = reply_text
        update = SimpleNamespace(
            effective_message=message, effective_user=user,
            effective_chat=SimpleNamespace(type="private", id=321),
        )
        context = SimpleNamespace(user_data={}, bot=SimpleNamespace())
        with patch.object(BOT, "claim_message_for_processing", return_value=True), \
             patch.object(BOT, "persist_context_message") as persist_user, \
             patch.object(BOT, "make_context", return_value="private history only"), \
             patch.object(BOT, "answer_private_question", new_callable=AsyncMock,
                          return_value="لا توجد بيانات مؤكدة كافية.") as answer, \
             patch.object(BOT, "persist_assistant_message") as persist_answer:
            await BOT.morehelp_message_handler(update, context)
        self.assertEqual(replies, ["لا توجد بيانات مؤكدة كافية."])
        answer.assert_awaited_once_with("Is TBL cancelled?", 321, "private history only")
        persist_user.assert_called_once_with(message)
        persist_answer.assert_called_once_with(321, 89, "لا توجد بيانات مؤكدة كافية.", 321)


    async def test_private_timetable_answer_uses_deterministic_result_without_gemini(self):
        expected = "لا توجد بيانات مؤكدة عن حالة TBL."
        with patch.object(BOT, "private_qa_sources", return_value=('{"faqs":[]}', expected)), \
             patch.object(BOT, "gemini_async") as model_client:
            answer = await BOT.answer_private_question("Is TBL cancelled for group 6?", 321)
        self.assertEqual(answer, expected)
        model_client.models.generate_content.assert_not_called()

    async def test_private_database_answer_rejects_a_fabricated_source_id(self):
        evidence = '{"faqs":[{"id":1,"question":"TBL","answer":"Not confirmed"}],"approved_announcements":[],"activity_updates":[],"timetable_records":[]}'
        response = SimpleNamespace(parsed=BOT.PrivateAnswer(
            answer="The TBL is cancelled.", answer_type="database", sources=["FAQ:999"], confidence=0.99
        ))
        model_client = SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(return_value=response)))
        with patch.object(BOT, "private_qa_sources", return_value=(evidence, None)), \
             patch.object(BOT, "wait_for_rpm_slot", new_callable=AsyncMock), \
             patch.object(BOT, "reserve_daily_request", return_value=True), \
             patch.object(BOT, "log_model_request"), \
             patch.object(BOT, "gemini_async", model_client):
            answer = await BOT.answer_private_question("Is TBL cancelled?", 321)
        self.assertIn("لم أجد مصدراً موثوقاً", answer)
        self.assertNotIn("The TBL is cancelled", answer)

    async def test_private_database_answer_accepts_only_a_real_source_id(self):
        evidence = '{"faqs":[{"id":1,"question":"TBL","answer":"Not confirmed"}],"approved_announcements":[],"activity_updates":[],"timetable_records":[]}'
        response = SimpleNamespace(parsed=BOT.PrivateAnswer(
            answer="The FAQ says it is not confirmed.", answer_type="database", sources=["FAQ:1"], confidence=0.99
        ))
        model_client = SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(return_value=response)))
        with patch.object(BOT, "private_qa_sources", return_value=(evidence, None)), \
             patch.object(BOT, "wait_for_rpm_slot", new_callable=AsyncMock), \
             patch.object(BOT, "reserve_daily_request", return_value=True), \
             patch.object(BOT, "log_model_request"), \
             patch.object(BOT, "gemini_async", model_client):
            answer = await BOT.answer_private_question("What does the FAQ say?", 321)
        self.assertIn("The FAQ says it is not confirmed.", answer)
        self.assertIn("FAQ:1", answer)

    async def test_explicit_morehelp_still_forwards_to_owner(self):
        replies = []

        async def reply_text(text, **kwargs):
            replies.append(text)
            return SimpleNamespace(message_id=99)

        async def send_message(**kwargs):
            self.assertEqual(kwargs["chat_id"], BOT.OWNER_ID)

        user = SimpleNamespace(id=321, full_name="Student", username=None, is_bot=False)
        message = SimpleNamespace(
            chat_id=321, message_id=98, from_user=user, text="Please contact a leader", caption=None,
            reply_to_message=None, photo=None, video=None, voice=None, audio=None, document=None,
            animation=None, sticker=None, reply_text=reply_text,
        )
        update = SimpleNamespace(
            effective_message=message, effective_user=user,
            effective_chat=SimpleNamespace(type="private", id=321),
        )
        context = SimpleNamespace(user_data={"awaiting_more_help": True}, bot=SimpleNamespace(send_message=send_message))
        with patch.object(BOT, "answer_private_question", new_callable=AsyncMock) as answer:
            await BOT.morehelp_message_handler(update, context)
        answer.assert_not_awaited()
        self.assertNotIn("awaiting_more_help", context.user_data)
        self.assertTrue(any("تم إرسال استفسارك" in text for text in replies))


class BoundedEvidenceSerializationTests(unittest.TestCase):
    def test_large_evidence_is_valid_json_and_within_budget(self):
        evidence = {
            "faqs": [
                {"id": index, "question": "q" * 1200, "answer": "a" * 1600}
                for index in range(80)
            ],
            "approved_announcements": [
                {"id": index, "summary": "s" * 1200, "original_text": "o" * 1800}
                for index in range(40)
            ],
            "activity_updates": [
                {"id": index, "details": "d" * 1500}
                for index in range(60)
            ],
            "timetable_records": [
                {"source_key": f"source-{index}", "source": "t" * 1200}
                for index in range(60)
            ],
            "exam_records": [
                {"source_key": f"exam-{index}", "source": "e" * 1200}
                for index in range(60)
            ],
        }

        serialized = BOT._bounded_evidence_json(evidence, max_chars=26000)

        self.assertLessEqual(len(serialized), 26000)
        parsed = json.loads(serialized)
        self.assertIsInstance(parsed, dict)
        self.assertIn("faqs", parsed)
        self.assertIn("timetable_records", parsed)

    def test_source_identifiers_survive_normal_bounding(self):
        evidence = {
            "faqs": [{"id": 17, "question": "When?", "answer": "Not confirmed."}],
            "timetable_records": [{"source_key": "sha256-source-key", "activity": "Anatomy"}],
        }

        parsed = json.loads(BOT._bounded_evidence_json(evidence))

        self.assertEqual(parsed["faqs"][0]["id"], 17)
        self.assertEqual(parsed["timetable_records"][0]["source_key"], "sha256-source-key")



if __name__ == "__main__":
    unittest.main()
