"""pixiv_uploader/humanize.py 的纯逻辑测试：不起浏览器，用 fake page/mouse/keyboard。"""

import math
import random
import threading
import unittest
from unittest.mock import Mock, patch

from pixiv_uploader import humanize
from pixiv_uploader.humanize import HumanSession, HumanTypingError, sleep_with_cancel


class FakeMouse:
    def __init__(self):
        self.moves: list[tuple[float, float]] = []
        self.clicks: list[tuple[float, float]] = []
        self.wheels: list[tuple[float, float]] = []

    def move(self, x, y):
        self.moves.append((x, y))

    def click(self, x, y):
        self.clicks.append((x, y))

    def wheel(self, dx, dy):
        self.wheels.append((dx, dy))


class FakeKeyboard:
    """可选绑定一个 FakeLocator：打字/退格/回车直接反映到它的 value 上。"""

    def __init__(self):
        self.events: list[tuple[str, str]] = []
        self.target = None

    def type(self, text, delay=None):
        self.events.append(("type", text))
        if self.target is not None:
            self.target.value += text

    def insert_text(self, text):
        self.events.append(("insert_text", text))
        if self.target is not None:
            self.target.value += text

    def press(self, key):
        self.events.append(("press", key))
        if self.target is None:
            return
        if key == "Backspace":
            self.target.value = self.target.value[:-1]
        elif key == "Enter":
            self.target.value += "\n"


class FakeLocator:
    def __init__(self, box=None, value=""):
        self.value = value
        self._box = box if box is not None else {"x": 100.0, "y": 100.0, "width": 40.0, "height": 20.0}
        self.fills: list[str] = []
        self.click_count = 0
        self.click_positions: list[dict | None] = []

    def bounding_box(self, timeout=None):
        return self._box

    def fill(self, value):
        self.fills.append(value)
        self.value = value

    def click(self, timeout=None, position=None):
        self.click_count += 1
        self.click_positions.append(position)

    def input_value(self):
        return self.value


class FakePage:
    def __init__(self):
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.evaluated: list[str] = []

    def evaluate(self, script, arg=None):
        self.evaluated.append(script)
        return {"x": 640.0, "y": 360.0}


def make_session(page=None, *, seed=1234, cancel_event=None, **params) -> HumanSession:
    session = HumanSession(page or FakePage(), cancel_event=cancel_event, rng=random.Random(seed))
    for key, value in params.items():
        setattr(session, key, value)
    return session


class RecordingSleep:
    """替换 humanize.sleep_with_cancel：记录时长并保留取消语义，不真实等待。"""

    def __init__(self):
        self.durations: list[float] = []

    def __call__(self, seconds, cancel_event, poll=0.2):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("task canceled")
        self.durations.append(seconds)


class SleepWithCancelTests(unittest.TestCase):
    def test_cancel_set_raises_immediately(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(InterruptedError):
            sleep_with_cancel(5.0, event)

    def test_short_sleep_completes(self):
        sleep_with_cancel(0.01, None)

    def test_cancel_mid_sleep_raises(self):
        event = threading.Event()
        timer = threading.Timer(0.05, event.set)
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                sleep_with_cancel(5.0, event, poll=0.01)
        finally:
            timer.cancel()


class BrowserClosedDetectionTests(unittest.TestCase):
    def test_target_closed_error_type_name(self):
        TargetClosedError = type("TargetClosedError", (Exception,), {})
        self.assertTrue(humanize.is_browser_closed_exception(TargetClosedError("boom")))

    def test_message_markers(self):
        self.assertTrue(humanize.is_browser_closed_exception(
            RuntimeError("Target page, context or browser has been closed")
        ))
        self.assertFalse(humanize.is_browser_closed_exception(ValueError("unrelated")))


class InteractiveChallengeTests(unittest.TestCase):
    def test_visible_challenge_is_detected(self):
        page = Mock()
        matches = Mock()
        matches.count.return_value = 1
        matches.nth.return_value.is_visible.return_value = True
        page.locator.return_value = matches

        self.assertTrue(humanize.has_visible_interactive_challenge(page))

    def test_absent_challenge_does_not_block_waiting_motion(self):
        page = Mock()
        matches = Mock()
        matches.count.return_value = 0
        page.locator.return_value = matches

        self.assertFalse(humanize.has_visible_interactive_challenge(page))


class TypingTests(unittest.TestCase):
    def _type(self, session, locator, text, recorder, **kwargs):
        session.page.keyboard.target = locator
        with patch.object(humanize, "sleep_with_cancel", recorder):
            session.type_text(locator, text, **kwargs)

    def test_delay_bounds_without_think_pauses(self):
        recorder = RecordingSleep()
        session = make_session(typing_base_ms=75.0, pause_tendency=1.0, typo_rate=0.0)
        session._next_think_at = 10**9  # 关闭思考停顿，单独校验字符间隔边界
        locator = FakeLocator()
        text = "hello world"
        self._type(session, locator, text, recorder)

        self.assertEqual(locator.value, text)
        self.assertEqual(len(recorder.durations), len(text))
        for char, delay in zip(text, recorder.durations):
            if char == " ":
                self.assertTrue(0.12 <= delay <= 0.35, f"空格后停顿越界: {delay}")
            else:
                self.assertTrue(0.03 <= delay <= 0.09, f"词内停顿越界: {delay}")

    def test_mixed_language_delay_uses_script_specific_bounds(self):
        session = make_session(typing_base_ms=75.0, pause_tendency=1.0)
        session._chars_since_think = 0
        session._next_think_at = 10**9

        samples = {
            "ascii": session._char_delay("a"),
            "kana": session._char_delay("あ"),
            "han": session._char_delay("画"),
            "punctuation": session._char_delay("。"),
        }

        self.assertTrue(0.03 <= samples["ascii"] <= 0.09)
        self.assertTrue(0.05 <= samples["kana"] <= 0.14)
        self.assertTrue(0.08 <= samples["han"] <= 0.20)
        self.assertTrue(0.12 <= samples["punctuation"] <= 0.35)

    def test_think_pause_stays_within_bounds(self):
        recorder = RecordingSleep()
        session = make_session(typing_base_ms=75.0, pause_tendency=1.0, typo_rate=0.0)
        locator = FakeLocator()
        text = "a" * 120
        self._type(session, locator, text, recorder)

        self.assertEqual(locator.value, text)
        self.assertEqual(len(recorder.durations), len(text))
        # 词内 0.09 + 思考 1.5 是上限；长文本下应至少触发过思考停顿逻辑而不越界
        self.assertTrue(all(0.03 <= d <= 0.09 + 1.5 for d in recorder.durations))

    def test_typo_sequence_only_for_ascii_and_text_still_correct(self):
        recorder = RecordingSleep()
        session = make_session(typo_rate=1.0, pause_tendency=1.0)
        session._next_think_at = 10**9
        locator = FakeLocator()
        text = "a1b"
        self._type(session, locator, text, recorder)

        self.assertEqual(locator.value, text)
        backspaces = [e for e in session.page.keyboard.events if e == ("press", "Backspace")]
        # 两个 ASCII 字母各触发一次“打错→退格”，数字不触发
        self.assertEqual(len(backspaces), 2)

    def test_typo_never_for_cjk(self):
        recorder = RecordingSleep()
        session = make_session(typo_rate=1.0, pause_tendency=1.0)
        session._next_think_at = 10**9
        locator = FakeLocator()
        text = "あいうえお日本語"
        self._type(session, locator, text, recorder)

        self.assertEqual(locator.value, text)
        backspaces = [e for e in session.page.keyboard.events if e == ("press", "Backspace")]
        inserted = [e for e in session.page.keyboard.events if e[0] == "insert_text"]
        self.assertEqual(backspaces, [])
        self.assertEqual("".join(value for _kind, value in inserted), text)

    def test_newline_uses_enter_key(self):
        recorder = RecordingSleep()
        session = make_session(typo_rate=0.0)
        locator = FakeLocator()
        self._type(session, locator, "a\nb", recorder)

        self.assertEqual(locator.value, "a\nb")
        self.assertIn(("press", "Enter"), session.page.keyboard.events)

    def test_verify_mismatch_retries_once_then_raises(self):
        recorder = RecordingSleep()
        session = make_session(typo_rate=0.0)
        locator = FakeLocator()  # 不绑定 keyboard，input_value 永远停留在 ""
        with patch.object(humanize, "sleep_with_cancel", recorder):
            with self.assertRaises(HumanTypingError):
                session.type_text(locator, "abc")
        # 两次尝试各清空一次
        self.assertEqual(locator.fills, ["", ""])

    def test_incremental_typing_never_clears_existing_value(self):
        recorder = RecordingSleep()
        session = make_session(typo_rate=0.0)
        locator = FakeLocator(value="既存")
        self._type(session, locator, "tag", recorder, clear=False)

        self.assertEqual(locator.value, "既存tag")
        self.assertEqual(locator.fills, [])

    def test_typing_cancel_raises(self):
        event = threading.Event()
        event.set()
        session = make_session(cancel_event=event, typo_rate=0.0)
        locator = FakeLocator()
        session.page.keyboard.target = locator
        with self.assertRaises(InterruptedError):
            session.type_text(locator, "abc")


class PacerTests(unittest.TestCase):
    def test_fatigue_monotonic_and_capped(self):
        session = make_session()
        samples = []
        for count in (0, 5, 10, 30, 60, 120, 500, 2000):
            session._action_count = count
            samples.append(session.fatigue)
        self.assertEqual(samples[0], 1.0)
        self.assertTrue(all(left <= right for left, right in zip(samples, samples[1:])))
        self.assertEqual(samples[-1], 1.8)

    def test_action_pause_counts_actions_and_cancel_raises(self):
        recorder = RecordingSleep()
        session = make_session(pause_tendency=1.0)
        with patch.object(humanize, "sleep_with_cancel", recorder):
            session.action_pause()
        self.assertEqual(session._action_count, 1)
        self.assertTrue(all(d >= 0.3 for d in recorder.durations))

        event = threading.Event()
        event.set()
        canceled = make_session(cancel_event=event)
        with self.assertRaises(InterruptedError):
            canceled.action_pause()
        with self.assertRaises(InterruptedError):
            canceled.before_submit()

    def test_before_submit_within_declared_bounds(self):
        recorder = RecordingSleep()
        session = make_session(pause_tendency=1.0)
        with patch.object(humanize, "sleep_with_cancel", recorder):
            for _ in range(20):
                session.before_submit()
        self.assertTrue(all(1.5 <= d <= 4.0 for d in recorder.durations))

    def test_between_posts_delay_bounds_and_rest(self):
        session = make_session()
        for _ in range(200):
            delay = session.between_posts_delay(10.0)
            self.assertTrue(10.0 <= delay <= 16.0 + 360.0)
            self.assertEqual(session._action_count, 0)

        # 强制触发长休息：random()<0.08 恒真
        with patch.object(session.rng, "random", return_value=0.0):
            rested = session.between_posts_delay(10.0)
        self.assertGreaterEqual(rested, 10.0 + 120.0)

        # base 为 0 时仍只可能由长休息产生等待
        with patch.object(session.rng, "random", return_value=1.0):
            self.assertEqual(session.between_posts_delay(0.0), 0.0)


class TrajectoryTests(unittest.TestCase):
    def test_start_end_and_finiteness(self):
        session = make_session(mouse_speed=1.0)
        start = (100.0, 200.0)
        target = (800.0, 600.0)
        for seed in range(20):
            session.rng = random.Random(seed)
            points = session.mouse.trajectory(start, target)
            self.assertEqual(points[0], start)
            self.assertEqual(points[-1], target)
            self.assertTrue(all(math.isfinite(x) and math.isfinite(y) for x, y in points))

    def test_step_count_grows_with_distance(self):
        session = make_session(mouse_speed=1.0)
        near = len(session.mouse.trajectory((0.0, 0.0), (100.0, 0.0)))
        far = len(session.mouse.trajectory((0.0, 0.0), (1200.0, 0.0)))
        self.assertGreater(far, near)

    def test_tiny_distance_single_point(self):
        session = make_session()
        self.assertEqual(session.mouse.trajectory((5.0, 5.0), (5.5, 5.5)), [(5.5, 5.5)])


class MouseClickTests(unittest.TestCase):
    def test_click_locator_moves_dwells_and_clicks(self):
        recorder = RecordingSleep()
        page = FakePage()
        session = make_session(page)
        locator = FakeLocator()
        with patch.object(humanize, "sleep_with_cancel", recorder):
            session.mouse.click_locator(page, locator)

        self.assertEqual(locator.click_count, 1)
        self.assertEqual(page.mouse.clicks, [])
        click_position = locator.click_positions[0]
        self.assertIsNotNone(click_position)
        click_x = 100.0 + click_position["x"]
        click_y = 100.0 + click_position["y"]
        # 目标是元素中心 ±3px
        self.assertTrue(abs(click_x - 120.0) <= 3.0)
        self.assertTrue(abs(click_y - 110.0) <= 3.0)
        self.assertGreater(len(page.mouse.moves), 5)
        # 移动终点 == Locator 的点击位置，且点击前有 dwell
        self.assertEqual(page.mouse.moves[-1], (click_x, click_y))
        self.assertTrue(any(script.startswith("() => { window._lastMouseX") for script in page.evaluated))

    def test_click_locator_exposes_actionability_failure_to_caller(self):
        page = FakePage()
        session = make_session(page)
        locator = FakeLocator()
        locator.click = Mock(side_effect=RuntimeError("element is covered"))

        with patch.object(humanize, "sleep_with_cancel", RecordingSleep()):
            with self.assertRaisesRegex(RuntimeError, "covered"):
                session.mouse.click_locator(page, locator)

    def test_click_locator_falls_back_to_locator_click_without_box(self):
        page = FakePage()
        session = make_session(page)

        class NoBoxLocator(FakeLocator):
            def bounding_box(self, timeout=None):
                raise RuntimeError("not laid out")

        locator = NoBoxLocator()
        with patch.object(humanize, "sleep_with_cancel", RecordingSleep()):
            session.mouse.click_locator(page, locator)

        self.assertEqual(locator.click_count, 1)
        self.assertEqual(page.mouse.clicks, [])

    def test_click_cancel_raises(self):
        event = threading.Event()
        event.set()
        page = FakePage()
        session = make_session(page, cancel_event=event)
        with self.assertRaises(InterruptedError):
            session.mouse.click_locator(page, FakeLocator(), cancel_event=event)


class ScrollTests(unittest.TestCase):
    def test_scroll_splits_into_bounded_chunks(self):
        page = FakePage()
        session = make_session(page)
        with patch.object(humanize, "sleep_with_cancel", RecordingSleep()):
            session.mouse.scroll(page, 1000.0)

        total = sum(dy for _dx, dy in page.mouse.wheels)
        self.assertAlmostEqual(total, 1000.0)
        self.assertTrue(all(dy > 0 for _dx, dy in page.mouse.wheels))
        # 每段 80~400px，最后一段可能更小；段数在总量推导边界内
        chunks = [dy for _dx, dy in page.mouse.wheels]
        self.assertTrue(all(c <= 400.0 for c in chunks))
        self.assertTrue(all(c >= 80.0 for c in chunks[:-1]))
        self.assertGreaterEqual(len(chunks), math.ceil(1000.0 / 400.0))
        self.assertLessEqual(len(chunks), math.ceil(1000.0 / 80.0))

    def test_scroll_direction_and_zero(self):
        page = FakePage()
        session = make_session(page)
        with patch.object(humanize, "sleep_with_cancel", RecordingSleep()):
            session.mouse.scroll(page, -300.0)
            session.mouse.scroll(page, 0.0)
        total = sum(dy for _dx, dy in page.mouse.wheels)
        self.assertAlmostEqual(total, -300.0)
        self.assertTrue(all(dy < 0 for _dx, dy in page.mouse.wheels))

    def test_scroll_cancel_raises(self):
        event = threading.Event()
        event.set()
        page = FakePage()
        session = make_session(page, cancel_event=event)
        with self.assertRaises(InterruptedError):
            session.mouse.scroll(page, 500.0, cancel_event=event)


class IdleDriftTests(unittest.TestCase):
    def test_zero_probability_never_moves(self):
        page = FakePage()
        session = make_session(page)
        session.mouse.idle_drift(page, probability=0.0)
        self.assertEqual(page.mouse.moves, [])

    def test_visible_challenge_blocks_drift_inside_mouse_helper(self):
        page = FakePage()
        session = make_session(page)
        with patch.object(
            humanize,
            "has_visible_interactive_challenge",
            return_value=True,
        ):
            session.mouse.idle_drift(page, probability=1.0)

        self.assertEqual(page.mouse.moves, [])

    def test_full_probability_moves_within_drift_range(self):
        page = FakePage()
        session = make_session(page)
        with patch.object(humanize, "sleep_with_cancel", RecordingSleep()):
            session.mouse.idle_drift(page, probability=1.0)
        self.assertGreater(len(page.mouse.moves), 0)
        end_x, end_y = page.mouse.moves[-1]
        self.assertTrue(abs(end_x - 640.0) <= 40.0 + 1e-6)
        self.assertTrue(abs(end_y - 360.0) <= 25.0 + 1e-6)


if __name__ == "__main__":
    unittest.main()
