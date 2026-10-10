import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from backend import app as server
from backend.budget import BudgetExceeded, UsageBudget, configured_budget


class BudgetTests(unittest.TestCase):
    def test_budget_reads_configuration_when_created(self):
        with patch.dict("os.environ", {"MAX_STT_SECONDS": "123", "USAGE_DB_PATH": "logs/custom.sqlite3"}):
            budget = configured_budget()
            self.assertEqual(budget.limits["stt_seconds"], 123)
            self.assertEqual(budget.path, Path("logs/custom.sqlite3"))

    def test_reservations_survive_restart_and_reject_excess(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.db"
            UsageBudget(path, {"tts_chars": 10}).reserve("tts_chars", 8)
            with self.assertRaises(BudgetExceeded):
                UsageBudget(path, {"tts_chars": 10}).reserve("tts_chars", 3)
            UsageBudget(path, {"tts_chars": 10}).reserve("tts_chars", 2)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_two_microphones_can_be_unmuted(self):
        room = server.Session("floor")
        for label in "ABC":
            p = server.Participant(label, Mock(), "en-IN")
            p.muted = True
            room.participants[label] = p
        self.assertTrue(room.set_microphone(room.participants["A"], False))
        self.assertTrue(room.set_microphone(room.participants["B"], False))
        self.assertFalse(room.set_microphone(room.participants["C"], False))
        self.assertTrue(room.participants["C"].muted)
        self.assertTrue(room.set_microphone(room.participants["A"], True))
        self.assertTrue(room.set_microphone(room.participants["C"], False))
        room.participants["A"].role = "listener"
        self.assertFalse(room.set_microphone(room.participants["A"], False))

    async def test_distinct_languages_share_one_paid_call(self):
        listeners = [server.Participant(label, Mock(send_json=AsyncMock()), language)
                     for label, language in [("B", "ta-IN"), ("C", "ta-IN"), ("D", "en-IN")]]
        client = SimpleNamespace(text=SimpleNamespace(translate=AsyncMock(return_value=SimpleNamespace(translated_text="test"))),
                                 text_to_speech=SimpleNamespace(convert=AsyncMock(return_value=SimpleNamespace(audios=["AA=="]))))
        with patch.object(server, "BUDGET", Mock()), patch.object(server, "log_metrics"):
            await server.translate_and_speak_to_listeners("hello", listeners, client, 1, 20, "room", "A", 123)
        self.assertEqual(client.text.translate.await_count, 2)
        self.assertEqual(client.text_to_speech.convert.await_count, 2)
        for listener in listeners:
            audio = listener.websocket.send_json.call_args_list[-1].args[0]
            self.assertEqual(audio["language"], listener.language)
            self.assertEqual(audio["capture_start_ms"], 123)

    async def test_one_language_failure_does_not_cancel_other_delivery(self):
        listeners = [server.Participant("B", Mock(send_json=AsyncMock()), "ta-IN"),
                     server.Participant("C", Mock(send_json=AsyncMock()), "en-IN")]
        async def translate(**kwargs):
            if kwargs["target_language_code"] == "ta-IN":
                raise RuntimeError("failure")
            return SimpleNamespace(translated_text="ok")
        client = SimpleNamespace(text=SimpleNamespace(translate=translate),
                                 text_to_speech=SimpleNamespace(convert=AsyncMock(return_value=SimpleNamespace(audios=["AA=="]))))
        with patch.object(server, "BUDGET", Mock()), patch.object(server, "log_metrics"):
            await server.translate_and_speak_to_listeners("hello", listeners, client, 1, None, "room", "A")
        self.assertEqual(listeners[0].websocket.send_json.call_args.args[0]["type"], "error")
        self.assertEqual(listeners[1].websocket.send_json.call_args.args[0]["type"], "audio")

    async def test_reconnect_keeps_participant_and_reads_replacement(self):
        old = Mock(receive=AsyncMock(return_value={"type": "websocket.disconnect"}))
        new = Mock(receive=AsyncMock(return_value={"type": "websocket.receive", "text": "hello"}))
        participant = server.Participant("A", old, "en-IN")
        pending = asyncio.create_task(participant.receive())
        await asyncio.sleep(.01)
        self.assertTrue(participant.detached)
        participant.websocket = new
        participant.connected.set()
        message = await pending
        self.assertEqual(message["text"], "hello")
        self.assertEqual(participant.party_id, "A")

    async def test_expired_reconnect_is_bounded(self):
        participant = server.Participant("A", Mock(receive=AsyncMock(return_value={"type": "websocket.disconnect"})), "en-IN")
        with patch.object(server, "RECONNECT_SECONDS", .01):
            with self.assertRaises(asyncio.TimeoutError):
                await participant.receive()


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        metrics = patch.multiple(server, LOG_DIR=directory.name,
                                 METRICS_LOG_PATH=str(Path(directory.name) / "metrics.jsonl"))
        metrics.start()
        self.addCleanup(metrics.stop)
        self.settings = patch.multiple(server, LIVE_ENABLED=True, API_KEY=None, ACCESS_TOKEN="offline-test-invite-token",
                                       RECONNECT_SECONDS=.01, MAX_ROOMS=2)
        self.settings.start()
        self.client = TestClient(server.app)
        self.client.__enter__()
        server.SESSIONS.clear()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.settings.stop()
        server.SESSIONS.clear()

    def join(self, ws, **overrides):
        ws.send_json({"type": "join", "language": "en-IN", "role": "listener",
                      "access_token": "offline-test-invite-token", **overrides})
        return ws.receive_json()

    def test_invalid_invite_rejected_before_sarvam(self):
        with patch.object(server, "AsyncSarvamAI") as provider:
            with self.client.websocket_connect("/ws/demo") as ws:
                self.assertEqual(self.join(ws, access_token="wrong")["type"], "error")
            provider.assert_not_called()

    def test_capture_assets_are_embedded_in_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("__PCM_WORKLET_SOURCE__", response.text)
        self.assertNotIn("<!-- INLINE_", response.text)
        self.assertIn('class="lucide lucide-mic"', response.text)
        self.assertIn('class="lucide lucide-mic-off"', response.text)
        self.assertIn('registerProcessor', response.text)

    def test_invalid_language_rejected(self):
        with self.client.websocket_connect("/ws/demo") as ws:
            self.assertEqual(self.join(ws, language="xx")["type"], "error")

    def test_listener_opens_no_upstream_and_ping_works(self):
        with patch.object(server, "AsyncSarvamAI") as provider:
            with self.client.websocket_connect("/ws/demo") as ws:
                self.assertEqual(self.join(ws)["party_id"], "A")
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "ping", "client_ms": 123})
                self.assertEqual(ws.receive_json()["type"], "pong")
                ws.send_json({"type": "end"})
            provider.return_value.speech_to_text_realtime_streaming.connect.assert_not_called()
        self.assertFalse(server.SESSIONS)

    def test_disallowed_origin_rejected(self):
        with self.client.websocket_connect("/ws/demo", headers={"origin": "https://evil.example"}) as ws:
            self.assertEqual(self.join(ws)["type"], "error")

    def test_live_kill_switch(self):
        with patch.object(server, "LIVE_ENABLED", False):
            with self.client.websocket_connect("/ws/demo") as ws:
                self.assertEqual(self.join(ws)["type"], "error")

    def test_listener_cannot_unmute(self):
        with self.client.websocket_connect("/ws/listener-mic") as ws:
            self.assertTrue(self.join(ws)["muted"])
            ws.receive_json()
            ws.send_json({"type": "microphone", "muted": False})
            reply = ws.receive_json()
            self.assertFalse(reply["accepted"])
            self.assertTrue(reply["muted"])
            ws.send_json({"type": "end"})

    def test_two_speakers_translate_independently_and_muted_pcm_is_zeroed(self):
        import base64
        streams = []

        class Stream:
            def __init__(self):
                self.events = asyncio.Queue()
                self.frames = []
                self.emitted = False
                self.text = f"speaker-{len(streams)}"
                streams.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                event = await self.events.get()
                if event is None:
                    raise StopAsyncIteration
                return event

            async def send_realtime_audio_input(self, audio):
                frame = base64.b64decode(audio.audio)
                self.frames.append(frame)
                if any(frame) and not self.emitted:
                    self.emitted = True
                    await self.events.put(SimpleNamespace(event="transcript.final", text=self.text))

            async def send_realtime_end(self, end):
                await self.events.put(None)

        provider = Mock()
        provider.speech_to_text_realtime_streaming.connect.side_effect = lambda **kwargs: Stream()
        provider.text.translate = AsyncMock(side_effect=lambda **kwargs: SimpleNamespace(translated_text=kwargs["input"]))
        provider.text_to_speech.convert = AsyncMock(return_value=SimpleNamespace(audios=["AA=="]))

        def until(ws, predicate):
            while True:
                message = ws.receive_json()
                if predicate(message):
                    return message

        with patch.object(server, "AsyncSarvamAI", return_value=provider), patch.object(server, "BUDGET", Mock()), patch.object(server, "log_metrics"):
            with self.client.websocket_connect("/ws/overlap") as listener:
                self.join(listener)
                listener.receive_json()
                with self.client.websocket_connect("/ws/overlap") as a, self.client.websocket_connect("/ws/overlap") as b:
                    self.join(a, role="speaker")
                    until(a, lambda m: m["type"] == "ready")
                    self.join(b, role="speaker")
                    until(b, lambda m: m["type"] == "ready")
                    a.send_json({"type": "microphone", "muted": True})
                    until(a, lambda m: m["type"] == "microphone" and m["party_id"] == "B")
                    a.send_bytes(b"\x01\x10" * 1600)
                    a.send_json({"type": "ping", "client_ms": 123})
                    until(a, lambda m: m["type"] == "pong")
                    self.assertFalse(any(streams[0].frames[-1]))
                    a.send_json({"type": "microphone", "muted": False})
                    until(a, lambda m: m["type"] == "microphone" and m["party_id"] == "B")
                    a.send_bytes(b"\x01\x10" * 1600)
                    b.send_bytes(b"\x02\x20" * 1600)
                    speakers = {until(listener, lambda m: m["type"] == "audio")["speaker"] for _ in range(2)}
                    self.assertEqual(speakers, {"B", "C"})
                    self.assertTrue(all(stream.emitted for stream in streams))
                    a.send_json({"type": "end"})
                    b.send_json({"type": "end"})
                listener.send_json({"type": "end"})
        self.assertFalse(server.SESSIONS)

    def test_room_limit(self):
        with patch.object(server, "MAX_ROOMS", 1):
            with self.client.websocket_connect("/ws/first") as first:
                self.join(first)
                first.receive_json()
                with self.client.websocket_connect("/ws/second") as second:
                    second.send_json({"type": "join", "language": "en-IN", "role": "listener", "access_token": "offline-test-invite-token"})
                    from starlette.websockets import WebSocketDisconnect
                    with self.assertRaises(WebSocketDisconnect):
                        second.receive_json()
                first.send_json({"type": "end"})

    def test_real_handler_flushes_final_transcript_and_routes_audio(self):
        class Stream:
            def __init__(self):
                self.events = asyncio.Queue()
                self.started = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                event = await self.events.get()
                if event is None:
                    raise StopAsyncIteration
                return event

            async def send_realtime_audio_input(self, audio):
                if not self.started:
                    self.started = True
                    await self.events.put(SimpleNamespace(event="vad.speech_start"))

            async def send_realtime_end(self, end):
                await self.events.put(SimpleNamespace(event="transcript.final", text="hello"))
                await self.events.put(None)

        provider = Mock()
        provider.speech_to_text_realtime_streaming.connect.side_effect = lambda **kwargs: Stream()
        provider.text.translate = AsyncMock(return_value=SimpleNamespace(translated_text="translated"))
        provider.text_to_speech.convert = AsyncMock(return_value=SimpleNamespace(audios=["AA=="]))
        with patch.object(server, "AsyncSarvamAI", return_value=provider), patch.object(server, "BUDGET", Mock()), patch.object(server, "log_metrics"):
            with self.client.websocket_connect("/ws/pipeline") as listener:
                self.join(listener)
                listener.receive_json()
                with self.client.websocket_connect("/ws/pipeline") as speaker:
                    self.join(speaker, role="speaker")
                    while speaker.receive_json()["type"] != "ready":
                        pass
                    speaker.send_bytes(b"\0\0" * 1600)
                    speaker.send_json({"type": "end"})
                    messages = []
                    while not messages or messages[-1]["type"] != "audio":
                        messages.append(listener.receive_json())
                    latency = next(m for m in messages if m["type"] == "latency")
                    self.assertIn("queue_ms", latency)
                    self.assertEqual(messages[-1]["speaker"], "B")
                    self.assertEqual(messages[-1]["language"], "en-IN")
                listener.send_json({"type": "end"})
            self.assertEqual(provider.speech_to_text_realtime_streaming.connect.call_count, 1)
        self.assertFalse(server.SESSIONS)

    def test_budget_failure_cleans_room_before_opening_stt(self):
        with patch.object(server, "BUDGET", Mock(reserve=Mock(side_effect=BudgetExceeded("limit")))), patch.object(server, "AsyncSarvamAI") as provider:
            with self.client.websocket_connect("/ws/budget") as ws:
                self.join(ws, role="speaker")
                self.assertEqual(ws.receive_json()["type"], "error")
            provider.return_value.speech_to_text_realtime_streaming.connect.assert_not_called()
        self.assertFalse(server.SESSIONS)

    def test_endpoint_resume_retains_identity_language_and_control_loop(self):
        import time
        with patch.object(server, "RECONNECT_SECONDS", 1):
            with self.client.websocket_connect("/ws/resume") as first:
                joined = self.join(first)
                first.receive_json()
                first.close(code=1006)
                for _ in range(100):
                    if server.SESSIONS["resume"].participants["A"].detached:
                        break
                    time.sleep(.005)
                with self.client.websocket_connect("/ws/resume") as replacement:
                    resumed = self.join(replacement, resume_token=joined["resume_token"], language="ta-IN")
                    self.assertTrue(resumed["resumed"])
                    self.assertEqual(resumed["party_id"], "A")
                    self.assertEqual(resumed["language"], "en-IN")
                    replacement.send_json({"type": "ping", "client_ms": 123})
                    self.assertEqual(replacement.receive_json()["type"], "pong")
                    replacement.send_json({"type": "end"})
        self.assertFalse(server.SESSIONS)


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "loadtest"))
        import run_structured_test
        cls.harness = run_structured_test

    def test_pilot_covers_every_category(self):
        self.assertEqual({e[0] for e in self.harness.select_entries(1, 5)}, {"hi", "en", "ta", "te", "mix"})

    def test_summary_counts_missing_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            self.harness.save_checkpoint([{"category": "hi", "index": 0, "results": {}, "complete": False}], path)
            self.assertEqual(len(json.loads(path.read_text())), 1)
            import csv
            with path.with_name("results_summary.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[-1]["n_missing"], "4")


class HarnessExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_campaign_checkpoints_and_paces_success(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "loadtest"))
        import run_structured_test as harness
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "campaign.json"
            result = {"category": "en", "index": 0, "results": {}, "complete": True}
            previous = [{"category": "en", "index": 0, "error": "timeout", "complete": False}]
            with patch.object(harness, "run_one_utterance", AsyncMock(return_value=result)):
                await harness.main(SimpleNamespace(min_utterance_seconds=.01), ([("en", 0, "test")], previous, output))
            self.assertTrue(json.loads(output.read_text())[0]["complete"])
            self.assertEqual(json.loads(output.read_text())[0]["prior_attempts"][0]["error"], "timeout")


if __name__ == "__main__":
    unittest.main()
