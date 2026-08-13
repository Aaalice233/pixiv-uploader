"""浏览器自动化拟人化原语。

为 Pixiv 与 Civitai 的浏览器投稿注入真人行为特征，降低被服务端风控
误判为机器人的概率：

- 拟人鼠标：贝塞尔轨迹 + 垂直微抖动 + 末端过冲修正 + 点击前 dwell
- 动态打字：变速间隔、标点停顿、思考停顿、ASCII 错别字退格修正
- 自适应节奏：疲劳模型（越操作越慢）、偶发走神、投稿间随机长休息

参数不做用户配置：每个 HumanSession 创建时随机抽样一组会话级基准值
（打字速度、鼠标速度、停顿倾向等），会话内一致、会话间不同，模拟
"不同时间段的真实用户"。

设计约束：
- 拟人化层的任何失败都必须允许调用方回退到原有直接操作，绝不因为
  拟人化而让投稿失败；browser closed 与 cancel 异常原样透传不吞。
- 本模块不引入风险等级、冷却或重试机制，也不触碰 UA/时区/locale 等
  浏览器指纹（指纹突变本身会触发风控）。
"""

from __future__ import annotations

import logging
import math
import random
import time

log = logging.getLogger(__name__)


def is_browser_closed_exception(exc: BaseException) -> bool:
    """判断异常是否由页面/浏览器被关闭引起（通用检测，不依赖 pixiv 层）。"""
    message = str(exc).lower()
    return type(exc).__name__ == "TargetClosedError" or any(
        marker in message
        for marker in (
            "target page, context or browser has been closed",
            "page has been closed",
            "browser has been closed",
            "context has been closed",
        )
    )


class HumanTypingError(RuntimeError):
    """拟人打字校验失败且重打仍不符；调用方必须回退到直接填充。"""


def _raise_if_canceled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("task canceled")


def sleep_with_cancel(seconds: float, cancel_event, poll: float = 0.2) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        _raise_if_canceled(cancel_event)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll, remaining))


# QWERTY 相邻键，仅用于 ASCII 字母的错别字模拟；CJK 走 IME，无对应错误模式。
_QWERTY_NEIGHBORS: dict[str, str] = {
    "q": "wa", "w": "qeas", "e": "wrds", "r": "etdf", "t": "ryfg",
    "y": "tugh", "u": "yihj", "i": "uojk", "o": "ipkl", "p": "ol",
    "a": "qwsz", "s": "awedzx", "d": "serfcx", "f": "drtgvc",
    "g": "ftyhvb", "h": "gyujnb", "j": "huiknm", "k": "jiolm",
    "l": "kop", "z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb",
    "b": "vghn", "n": "bhjm", "m": "njk",
}

# 输入这些字符后真人通常会停顿一下（换气/看结果）。
_PUNCT_PAUSE_CHARS = set(" \t\n.,，。、!！?？:：;；)）]】」』")


class HumanMouse:
    """拟人鼠标：贝塞尔轨迹 + 微抖动 + 末端过冲修正 + 点击前 dwell。"""

    def __init__(self, session: "HumanSession") -> None:
        self._session = session

    @property
    def _rng(self) -> random.Random:
        return self._session.rng

    def _current_position(self, page) -> tuple[float, float]:
        try:
            current = page.evaluate(
                "() => ({x: window._lastMouseX || 640, y: window._lastMouseY || 360})"
            )
            return float(current["x"]), float(current["y"])
        except Exception as exc:
            if is_browser_closed_exception(exc):
                raise
            return 640.0, 360.0

    def _remember_position(self, page, x: float, y: float) -> None:
        try:
            page.evaluate(
                f"() => {{ window._lastMouseX = {x}; window._lastMouseY = {y}; }}"
            )
        except Exception as exc:
            if is_browser_closed_exception(exc):
                raise

    def trajectory(
        self,
        start: tuple[float, float],
        target: tuple[float, float],
    ) -> list[tuple[float, float]]:
        """生成完整轨迹点：贝塞尔主体 + 垂直微抖动 + 末端过冲后修正回目标。"""
        rng = self._rng
        x0, y0 = start
        x1, y1 = target
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist < 2.0:
            return [(x1, y1)]
        # Fitts 定律近似：目标越远移动步数越多；mouse_speed 越大越慢（步数更多）
        steps = max(10, min(64, int(10 + math.log2(dist / 40.0 + 1.0) * 9 * self._session.mouse_speed)))
        cp1x = x0 + (x1 - x0) * 0.3 + rng.uniform(-0.15, 0.15) * dist
        cp1y = y0 + (y1 - y0) * 0.3 + rng.uniform(-0.12, 0.12) * dist
        cp2x = x0 + (x1 - x0) * 0.7 + rng.uniform(-0.10, 0.10) * dist
        cp2y = y0 + (y1 - y0) * 0.7 + rng.uniform(-0.08, 0.08) * dist
        # 末端过冲：沿移动方向越过目标 2~8px，再走 2~4 小步修正回来
        overshoot = rng.uniform(2.0, 8.0) if dist > 24 else 0.0
        ux, uy = (x1 - x0) / dist, (y1 - y0) / dist
        over_x, over_y = x1 + ux * overshoot, y1 + uy * overshoot
        points: list[tuple[float, float]] = []
        for i in range(steps + 1):
            t = i / steps
            bx = (1 - t) ** 3 * x0 + 3 * (1 - t) ** 2 * t * cp1x + 3 * (1 - t) * t ** 2 * cp2x + t ** 3 * over_x
            by = (1 - t) ** 3 * y0 + 3 * (1 - t) ** 2 * t * cp1y + 3 * (1 - t) * t ** 2 * cp2y + t ** 3 * over_y
            if 0 < i < steps:
                # 每步 0.5~1.5px 垂直于移动方向的微抖动
                jitter = rng.uniform(0.5, 1.5) * rng.choice((-1.0, 1.0))
                bx += -uy * jitter
                by += ux * jitter
            points.append((bx, by))
        if overshoot > 0:
            correct_steps = rng.randint(2, 4)
            for i in range(1, correct_steps + 1):
                t = i / correct_steps
                points.append((over_x + (x1 - over_x) * t, over_y + (y1 - over_y) * t))
        else:
            points[-1] = (x1, y1)
        return points

    def move_to(self, page, x: float, y: float, *, cancel_event=None) -> None:
        start = self._current_position(page)
        points = self.trajectory(start, (x, y))
        for px, py in points:
            page.mouse.move(px, py)
            # 每步 5~20ms，随会话鼠标速度缩放
            sleep_with_cancel(
                self._rng.uniform(0.005, 0.02) * self._session.mouse_speed,
                cancel_event,
            )
        self._remember_position(page, x, y)

    def click_locator(self, page, locator, *, cancel_event=None) -> None:
        """拟人移动到元素中心附近并点击；取不到位置时回退 locator.click()。"""
        _raise_if_canceled(cancel_event)
        try:
            box = locator.bounding_box(timeout=3000)
        except Exception as exc:
            if is_browser_closed_exception(exc):
                raise
            box = None
        if not box:
            locator.click()
            return
        target_x = box["x"] + box["width"] / 2 + self._rng.uniform(-3, 3)
        target_y = box["y"] + box["height"] / 2 + self._rng.uniform(-3, 3)
        self.move_to(page, target_x, target_y, cancel_event=cancel_event)
        # 点击前 dwell 50~200ms（真人瞄准后的停顿）
        sleep_with_cancel(self._rng.uniform(0.05, 0.2), cancel_event)
        page.mouse.click(target_x, target_y)

    def idle_drift(self, page, *, cancel_event=None, probability: float = 0.3) -> None:
        """长等待期间低概率小幅漂移鼠标，避免鼠标完全冻结。

        漂移是装饰性行为：除浏览器关闭与取消外，任何失败都不影响主流程。
        """
        if self._rng.random() >= probability:
            return
        try:
            x, y = self._current_position(page)
            self.move_to(
                page,
                x + self._rng.uniform(-40, 40),
                y + self._rng.uniform(-25, 25),
                cancel_event=cancel_event,
            )
        except InterruptedError:
            raise
        except Exception as exc:
            if is_browser_closed_exception(exc):
                raise

    def scroll(self, page, total_y: float, *, cancel_event=None) -> None:
        """拟人滚轮：拆成多段，每段 80~400px，段间 60~200ms 停顿。"""
        remaining = abs(float(total_y))
        if remaining <= 0:
            return
        sign = 1.0 if total_y > 0 else -1.0
        while remaining > 0:
            chunk = min(remaining, self._rng.uniform(80.0, 400.0))
            page.mouse.wheel(0, sign * chunk)
            remaining -= chunk
            if remaining > 0:
                sleep_with_cancel(self._rng.uniform(0.06, 0.2), cancel_event)


class HumanSession:
    """一个浏览器会话的拟人化状态：会话级抽样参数 + 自适应节奏模型。

    每浏览器会话创建一个实例并贯穿使用：会话内行为基准一致，会话间随机。
    """

    def __init__(self, page, *, cancel_event=None, rng: random.Random | None = None) -> None:
        self.page = page
        self.cancel_event = cancel_event
        self.rng = rng or random.Random()
        # ── 会话指纹抽样：每会话不同、会话内一致 ──
        self.typing_base_ms = self.rng.uniform(55.0, 105.0)
        self.mouse_speed = self.rng.uniform(0.85, 1.35)
        self.pause_tendency = self.rng.uniform(0.8, 1.3)
        self.typo_rate = self.rng.uniform(0.004, 0.01)
        self.mouse = HumanMouse(self)
        self._action_count = 0
        self._chars_since_think = 0
        self._next_think_at = self.rng.randint(8, 20)

    # ── 节奏（Pacer） ─────────────────────────────────────────────

    @property
    def fatigue(self) -> float:
        """疲劳系数：随本会话连续动作数缓慢上升，上限 1.8×，休息后重置。"""
        return min(1.8, 1.0 + 0.015 * self._action_count)

    def _sleep(self, seconds: float) -> None:
        sleep_with_cancel(seconds, self.cancel_event)

    def action_pause(self) -> None:
        """普通动作之间的间隔：0.3~0.9s × 疲劳 × 停顿倾向；约 2% 概率走神 3~8s。"""
        self._sleep(self.rng.uniform(0.3, 0.9) * self.fatigue * self.pause_tendency)
        if self.rng.random() < 0.02:
            self._sleep(self.rng.uniform(3.0, 8.0) * self.pause_tendency)
        self._action_count += 1

    def paced_sleep(self, base: float, jitter: float = 0.4) -> None:
        """带节奏的步骤间隔：base ± jitter 抖动 × 疲劳系数，并计入动作数。"""
        delta = self.rng.uniform(-jitter, jitter) * base
        self._sleep(max(0.05, base + delta) * self.fatigue)
        self._action_count += 1

    def before_submit(self) -> None:
        """提交前"通读检查"停顿：1.5~4s × 停顿倾向。"""
        self._sleep(self.rng.uniform(1.5, 4.0) * self.pause_tendency)
        self._action_count += 1

    def between_posts_delay(self, base: float) -> float:
        """相邻成功投稿的间隔秒数（只计算不睡眠，由调用方调度）。

        base × uniform(0.8, 1.6)；约 8% 概率叠加 2~6 分钟长休息。
        """
        delay = max(0.0, float(base)) * self.rng.uniform(0.8, 1.6)
        if self.rng.random() < 0.08:
            extra = self.rng.uniform(120.0, 360.0)
            log.info("    拟人节奏: 本次投稿后长休息 %.0f 秒", extra)
            delay += extra
        self._action_count = 0
        return delay

    # ── 打字 ───────────────────────────────────────────────────────

    def _char_delay(self, typed_char: str) -> float:
        """打完一个字符后的停顿（秒）：词内爆发 / 标点后拉长 / 偶发思考停顿。"""
        scale = self.typing_base_ms / 75.0
        if typed_char in _PUNCT_PAUSE_CHARS:
            delay = self.rng.uniform(0.12, 0.35) * scale
        else:
            delay = self.rng.uniform(0.03, 0.09) * scale
        if self._chars_since_think >= self._next_think_at and self.rng.random() < 0.35:
            delay += self.rng.uniform(0.5, 1.5) * self.pause_tendency
            self._chars_since_think = 0
            self._next_think_at = self.rng.randint(8, 20)
        return delay

    def _should_typo(self, char: str) -> bool:
        # 仅 ASCII 字母模拟错别字；CJK 经 IME 输入，没有对应的打错模式
        return char.isascii() and char.isalpha() and self.rng.random() < self.typo_rate

    def _type_typo_sequence(self, keyboard, char: str) -> None:
        """打错相邻键 → 停顿意识到 → 退格 → 修正前停顿（正确字符由主流程打）。"""
        wrong = self.rng.choice(_QWERTY_NEIGHBORS.get(char.lower(), char))
        if char.isupper():
            wrong = wrong.upper()
        keyboard.type(wrong)
        self._sleep(self.rng.uniform(0.10, 0.30))
        keyboard.press("Backspace")
        self._sleep(self.rng.uniform(0.08, 0.22))

    def _type_char(self, keyboard, char: str) -> None:
        # textarea 的换行必须走 Enter 键，keyboard.type("\n") 不会产出换行
        if char == "\n":
            keyboard.press("Enter")
        else:
            keyboard.type(char)

    def _type_once(self, locator, text: str, *, allow_typos: bool) -> None:
        keyboard = self.page.keyboard
        try:
            locator.fill("")
        except Exception as exc:
            if is_browser_closed_exception(exc):
                raise
        self._chars_since_think = 0
        self._next_think_at = self.rng.randint(8, 20)
        for char in text:
            if allow_typos and self._should_typo(char):
                self._type_typo_sequence(keyboard, char)
            self._type_char(keyboard, char)
            self._sleep(self._char_delay(char))
            self._chars_since_think += 1

    def _verify_typed(self, locator, text: str) -> bool:
        try:
            actual = locator.input_value()
        except Exception as exc:
            if is_browser_closed_exception(exc):
                raise
            # contenteditable 等读不到值的元素跳过校验，由调用方自行兜底
            return True
        return actual == text

    def type_text(
        self,
        locator,
        text: str,
        *,
        allow_typos: bool = True,
        verify: bool = True,
    ) -> None:
        """逐字符拟人输入；校验不符自动清空重打一次，仍不符抛 HumanTypingError。"""
        for attempt in (1, 2):
            self._type_once(locator, text, allow_typos=allow_typos and attempt == 1)
            if not verify or self._verify_typed(locator, text):
                return
            if attempt == 1:
                log.info("    拟人打字校验不符，清空后重打一次")
        raise HumanTypingError(f"typed value mismatch after retry (expected {len(text)} chars)")

    # ── 滚动 ───────────────────────────────────────────────────────

    def scroll(self, total_y: float) -> None:
        self.mouse.scroll(self.page, total_y, cancel_event=self.cancel_event)
