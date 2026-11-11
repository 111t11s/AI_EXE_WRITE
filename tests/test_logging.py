"""无需数据库或模型请求的日志集成验证。"""

import asyncio
import io
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.logging import REQUEST_ID, setup_logging
from core.middleware import RequestLoggingMiddleware


class LoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.log_path = Path(self.directory.name) / "application.log"
        self.output = io.StringIO()
        self.parent = logging.getLogger("ai_write")
        self.old_handlers = self.parent.handlers[:]
        self.old_level = self.parent.level
        self.old_propagate = self.parent.propagate
        self.parent.handlers = []
        self.config = SimpleNamespace(
            LOG_LEVEL="DEBUG", LOG_FORMAT="%(levelname)s %(name)s %(message)s",
            LOG_FILE=str(self.log_path), LOG_MAX_BYTES=1024, LOG_BACKUP_COUNT=2,
        )
        self.settings_patch = patch("core.logging.get_settings", return_value=self.config)
        self.stdout_patch = patch("core.logging.sys.stdout", self.output)
        self.settings_patch.start()
        self.stdout_patch.start()
        setup_logging()

    def tearDown(self):
        for handler in self.parent.handlers[:]:
            self.parent.removeHandler(handler)
            handler.close()
        self.parent.handlers = self.old_handlers
        self.parent.setLevel(self.old_level)
        self.parent.propagate = self.old_propagate
        self.stdout_patch.stop()
        self.settings_patch.stop()
        self.directory.cleanup()

    async def request(self, app, request_id="test-request", **scope_values):
        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        scope = {
            "type": "http", "method": "GET", "path": "/books",
            "headers": [(b"x-request-id", request_id.encode())],
            "query_string": b"api_key=do-not-log-query",
            **scope_values,
        }
        await RequestLoggingMiddleware(app)(scope, receive, send)
        return messages

    def test_shared_handlers_and_file_rotation(self):
        first = setup_logging("first")
        second = setup_logging("second")
        setup_logging("first")
        self.assertEqual(len(self.parent.handlers), 2)
        self.assertEqual(first.handlers, [])
        first.info("single-message")
        self.assertEqual(self.output.getvalue().count("single-message"), 1)
        for index in range(40):
            second.info("轮转日志 %s %s", index, "x" * 100)
        self.assertTrue(self.log_path.exists())
        self.assertTrue(Path(str(self.log_path) + ".1").exists())
        self.assertLessEqual(len(list(Path(self.directory.name).glob("application.log*"))), 3)

    async def test_concurrent_requests_and_streaming_keep_context_isolated(self):
        logger = setup_logging("test")

        async def app(scope, receive, send):
            await asyncio.sleep(0)
            logger.info("业务日志")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"one", "more_body": True})
            await send({"type": "http.response.body", "body": b"two", "more_body": False})

        results = await asyncio.gather(self.request(app, "request-a"), self.request(app, "request-b"))
        for messages, expected_id in zip(results, (b"request-a", b"request-b")):
            self.assertIn((b"x-request-id", expected_id), messages[0]["headers"])
            self.assertEqual([message["body"] for message in messages[1:]], [b"one", b"two"])
        text = self.output.getvalue()
        self.assertIn("业务日志 request_id=request-a", text)
        self.assertIn("业务日志 request_id=request-b", text)
        self.assertNotIn("do-not-log-query", text)
        self.assertEqual(REQUEST_ID.get(), "-")

    async def test_failure_keeps_traceback_without_exception_payload(self):
        async def fail(scope, receive, send):
            raise ValueError("private-chapter-content")

        with self.assertRaises(ValueError):
            await self.request(fail)
        text = self.output.getvalue()
        self.assertIn("Traceback", text)
        self.assertIn("ValueError", text)
        self.assertIn("outcome=failed", text)
        self.assertNotIn("private-chapter-content", text)
        self.assertEqual(REQUEST_ID.get(), "-")

    async def test_invalid_request_id_is_replaced(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 404})
            await send({"type": "http.response.body", "body": b""})

        messages = await self.request(app, "bad\nrequest-id")
        request_id = dict(messages[0]["headers"])[b"x-request-id"]
        self.assertRegex(request_id.decode(), r"^[a-f0-9]{32}$")
        self.assertIn("WARNING", self.output.getvalue())

    async def test_cancelled_request_restores_context(self):
        async def app(scope, receive, send):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await self.request(app)
        self.assertIn("outcome=cancelled", self.output.getvalue())
        self.assertEqual(REQUEST_ID.get(), "-")

    async def test_agent_success_and_failure_do_not_log_contents(self):
        from agent.agentscope_agents import run_agent

        class FakeAgent:
            name = "chapter_writer"

            async def __call__(self, message):
                return message

        class FailingAgent(FakeAgent):
            async def __call__(self, message):
                raise RuntimeError("private-model-response")

        await run_agent(FakeAgent(), "private-prompt", {"book_id": 1, "content": "private-novel"})
        with self.assertRaises(RuntimeError):
            await run_agent(FailingAgent(), "private-prompt")
        text = self.output.getvalue()
        self.assertIn("Agent 完成", text)
        self.assertIn("Agent 失败", text)
        self.assertIn("book_id", text)
        for secret in ("private-prompt", "private-novel", "private-model-response"):
            self.assertNotIn(secret, text)

    def test_database_error_is_logged_and_reraised(self):
        import pymysql
        from db.DataBaseManager import DatabaseManager


        with patch("db.DataBaseManager.pymysql.connect", side_effect=pymysql.OperationalError(1045, "private-db-password")):
            with self.assertRaises(pymysql.OperationalError):
                with DatabaseManager().get_connection():
                    self.fail("连接失败后不应进入业务代码")
        self.assertIn("数据库操作失败", self.output.getvalue())
        self.assertNotIn("private-db-password", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
