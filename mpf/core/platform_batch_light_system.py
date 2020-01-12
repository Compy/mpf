"""A light system for platforms which batches all updates."""
import abc
import asyncio
from typing import Callable, Tuple
from mpf.platforms.interfaces.light_platform_interface import LightPlatformInterface


class PlatformBatchLight(LightPlatformInterface, abc.ABC):

    """Light which can be batched."""

    __slots__ = ["light_system", "_callback", "_last_brightness"]

    def __init__(self, number, light_system: "PlatformBatchLightSystem"):
        """Initialise light."""
        super().__init__(number)
        self.light_system = light_system
        self._callback = None
        self._last_brightness = None

    @abc.abstractmethod
    def get_max_fade_ms(self):
        """Return max fade ms."""

    def set_fade(self, color_and_fade_callback: Callable[[int], Tuple[float, int]]):
        """Mark dirty and remember callback."""
        self.light_system.mark_dirty(self)
        self._callback = color_and_fade_callback
        self._last_brightness = None

    def get_fade_and_brightness(self, current_time):
        """Return fade + brightness and mark as clean if this is it."""
        if self._last_brightness:
            return self._last_brightness, 0, True
        max_fade_ms = self.get_max_fade_ms()
        brightness, fade_ms, done = self._callback(max_fade_ms, current_time=current_time)
        if done:
            self._last_brightness = brightness

        return brightness, fade_ms, done


class PlatformBatchLightSystem:

    """Batch light system for platforms."""

    __slots__ = ["dirty_lights", "dirty_schedule", "clock", "is_sequential_function", "update_task", "update_callback",
                 "sort_function", "update_hz", "max_batch_size"]

    # pylint: disable-msg=too-many-arguments
    def __init__(self, clock, sort_function, is_sequential_function, update_callback, update_hz, max_batch_size):
        """Initialise light system."""
        self.dirty_lights = []    # type: List[PlatformBatchLight]
        self.dirty_schedule = []
        self.is_sequential_function = is_sequential_function
        self.sort_function = sort_function
        self.update_task = None
        self.clock = clock
        self.update_callback = update_callback
        self.update_hz = update_hz
        self.max_batch_size = max_batch_size

    def start(self):
        """Start light system."""
        self.update_task = self.clock.loop.create_task(self._send_updates())
        self.update_task.add_done_callback(self._done)

    def stop(self):
        """Stop light system."""
        if self.update_task:
            self.update_task.cancel()
            self.update_task = None

    @staticmethod
    def _done(future):
        try:
            future.result()
        except asyncio.CancelledError:
            pass

    async def _send_updates(self):
        while True:
            if self.dirty_schedule:
                sorted(self.dirty_schedule, key=lambda x: x[0] + self.sort_function(x[1]))
            while self.dirty_schedule and self.dirty_schedule[0][0] <= self.clock.get_time():
                self.dirty_lights.append(self.dirty_schedule[0][1])
                del self.dirty_schedule[0]

            if self.dirty_lights:
                sorted(self.dirty_lights, key=self.sort_function)

            sequential_lights = []
            last_light = None
            for light in self.dirty_lights:
                if light == last_light:
                    continue
                last_light = light
                if not sequential_lights:
                    # first light
                    sequential_lights = [light]
                elif self.is_sequential_function(sequential_lights[-1], light):
                    # lights are sequential
                    sequential_lights.append(light)
                else:
                    # sequence ended
                    await self._send_update_batch(sequential_lights)
                    # this light is a new sequence
                    sequential_lights = [light]

            if sequential_lights:
                await self._send_update_batch(sequential_lights)

            self.dirty_lights.clear()

            await asyncio.sleep(.001, loop=self.clock.loop)

    async def _send_update_batch(self, sequential_lights):
        sequential_brightness_list = []
        common_fade_ms = None
        current_time = self.clock.get_time()
        for light in sequential_lights:
            brightness, fade_ms, done = light.get_fade_and_brightness(current_time)
            if not done:
                self.dirty_schedule.append((current_time + (fade_ms / 1000), light))
            if common_fade_ms is None:
                common_fade_ms = fade_ms

            if common_fade_ms == fade_ms and len(sequential_brightness_list) < self.max_batch_size:
                sequential_brightness_list.append((light, brightness, common_fade_ms))
            else:
                await self.update_callback(sequential_brightness_list)
                # start new list
                current_time = self.clock.get_time()
                common_fade_ms = fade_ms
                sequential_brightness_list = [(light, brightness, common_fade_ms)]

        if sequential_brightness_list:
            await self.update_callback(sequential_brightness_list)

    def mark_dirty(self, light: "PlatformBatchLight"):
        """Mark as dirty."""
        self.dirty_lights.append(light)
