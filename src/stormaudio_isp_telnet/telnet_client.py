"""Classes for communicating with the Storm Audio ISP series sound processors"""

from __future__ import annotations
from async_timeout import timeout
from asyncio import create_task, Event, sleep, Task, TimeoutError
from decimal import *
from enum import IntFlag, auto
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Dict, Callable

import typing

import telnetlib3

from .constants import *
from .line_reader import *


class DeviceState:
    def __init__(
        self
    ):
        self.brand: str = None
        self.model: str = None
        self.power_command: PowerCommand = None
        self.processor_state: ProcessorState = None
        self.volume_db: Decimal = None
        self.mute: bool = None
        self.inputs: list(Input) = None
        self.input_id: int = None
        self.input_zone2_id: int = None
        self.zones: list(Zone) = None
        self.presets: list(Preset) = None
        self.preset_id: int = None


class Input:
    def __init__(
        self,
        name: str,
        id: int,
        video_in_id: VideoInputID,
        audio_in_id: AudioInputID,
        audio_zone2_in_id: AudioZone2InputID,
        delay_ms: Decimal
    ):
        self.name: str = name
        self.id: int = id
        self.video_in_id: VideoInputID = video_in_id
        self.audio_in_id: AudioInputID = audio_in_id
        self.audio_zone2_in_id: AudioZone2InputID = audio_zone2_in_id
        self.delay_ms: Decimal = delay_ms


class Zone:
    def __init__(
        self,
        id: int,
        name: str,
        zone_layout_type: ZoneLayoutType,
        zone_type: ZoneType,
        use_zone2_source: bool,
        volume_db: Decimal,
        delay_ms: Decimal,
        mute: bool
    ):
        self.name: str = name
        self.id: int = id
        self.zone_layout_type: VideoInputID = zone_layout_type
        self.zone_type: AudioInputID = zone_type
        self.use_zone2_source: AudioZone2InputID = use_zone2_source
        self.volume_db = volume_db
        self.delay_ms: Decimal = delay_ms
        self.mute: bool = mute


class Preset:
    def __init__(
        self,
        name: str,
        id: int,
        audio_zone_ids: list(int),
        sphereaudio_theater_enabled: bool
    ):
        self.name: str = name
        self.id: int = id
        self.audio_zone_ids: list(int) = audio_zone_ids
        self.sphereaudio_theater_enabled: bool = sphereaudio_theater_enabled


class ReadLinesResult(IntFlag):
    NONE = 0
    COMPLETE = auto()
    STATE_UPDATED = auto()
    INCOMPLETE = auto()
    IGNORED = auto()


class TelnetClient():
    """Represents a client for communicating with the telnet server of an
        Storm Audio ISP sound processor."""

    def __init__(
        self,
        host: str,
        async_on_device_state_updated,
        async_on_disconnected,
        async_on_raw_line_received=None
    ):
        self._device_state: DeviceState = DeviceState()
        self._reader = None
        self._writer = None
        self._host: str = host
        self._remaining_output: str = None
        self._read_lines: TokenizedLinesReader = None
        self._async_on_device_state_updated = async_on_device_state_updated
        self._async_on_disconnected = async_on_disconnected
        self._async_on_raw_line_received = async_on_raw_line_received
        self._keepalive_loop_task: Task = None
        self._keepalive_received: bool = False
        self._read_loop_finished: Event = Event()

    def get_device_state(
        self
    ) -> DeviceState:
        return self._device_state

    async def async_connect(
        self
    ) -> None:
        """Connects to the telnet server and reads data on the async
        event loop."""
        self._read_lines = TokenizedLinesReader()
        self._remaining_output = ''

        self._read_loop_finished.clear()

        try:
            async with timeout(5):
                self._reader, self._writer = await telnetlib3.open_connection(
                    self._host,
                    connect_minwait=0.0,
                    connect_maxwait=0.0,
                    shell=self._read_loop
                )
        except (TimeoutError, OSError) as exc:
            raise ConnectionError from exc

        # Prime lists on connect (now that writer exists)
        await self._async_send_command('ssp.zones.list')
        await self._async_send_command('ssp.input.list')

        self._keepalive_received = False
        self._keepalive_loop_task = create_task(self._keepalive_loop())

    async def _keepalive_loop(
        self
    ):
        while True:

            await self._async_send_command("ssp.keepalive")
            await sleep(5)

            if not self._keepalive_received:
                # disconnect will cancel this task
                create_task(self.async_disconnect())
            self._keepalive_received = False

    async def async_disconnect(
        self
    ) -> None:
        """Disconnects from the telnet server."""
        if self._keepalive_loop_task is not None:
            self._keepalive_loop_task.cancel()
            self._keepalive_loop_task = None
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self._keepalive_received = False
        await self._read_loop_finished.wait()

    async def _read_loop(
        self,
        reader,
        writer
    ) -> None:
        """Async loop to read data received from the telnet server;
        sets device state as a result of data received."""

        exception: Exception = None
        while True:
            try:
                read_output = await reader.read(1024)
                if not read_output:
                    # EOF
                    break

                # Append new read output to any prior remaining output
                output = self._remaining_output + read_output

                # Parse the complete lines from the output
                output_lines = output.split('\n')

                # Add all complete lines to the read lines; excludes final
                # index, which is partial output (no CR yet)
                line_count = len(output_lines)
                if (line_count > 1):
                    if self._async_on_raw_line_received is not None:
                        for line_idx in range(0, line_count - 1):
                            await self._async_on_raw_line_received(
                                output_lines[line_idx])
                    self._read_lines.add_lines(output_lines[0: line_count - 1])

                # Save the remaining partial output
                self._remaining_output = output_lines[len(output_lines) - 1]

                state_updated: bool = False
                while self._read_lines.has_next_line():
                    read_result: ReadLinesResult = ReadLinesResult.NONE

                    read_result |= self._eval__line(
                        ['ssp', 'keepalive'],
                        self._eval_keepalive
                    )

                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'brand'],
                        lambda x: self._device_state.__setattr__('brand', x),
                        lambda x: x.strip('"')
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'model'],
                        lambda x: self._device_state.__setattr__('model', x),
                        lambda x: x.strip('"')
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'power'],
                        self._eval_power_command
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'procstate'],
                        self._eval_processor_state
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'vol'],
                        lambda x: self._device_state.__setattr__(
                            'volume_db', x),
                        lambda x: Decimal(x)
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'mute'],
                        self._eval_mute
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'input', 'start'],
                        self._eval_inputs
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'zones', 'start'],
                        self._eval_zones
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'preset', 'start'],
                        self._eval_presets
                    )

                                        preset_read_result: ReadLinesResult = self._eval__single_bracket_field(
                        ['ssp', 'preset'],
                        lambda x: self._device_state.__setattr__(
                            'preset_id', x),
                        lambda x: int(x)
                    )
                    read_result |= preset_read_result
                    # If the preset changes, request the zones list explicitly; the ISP
                    # does not refresh the available zones when the preset changes
                    if preset_read_result & ReadLinesResult.COMPLETE:
                        await self.async_request_zones()

                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'input'],
                        lambda x: self._device_state.__setattr__(
                            'input_id', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'inputZone2'],
                        lambda x: self._device_state.__setattr__(
                            'input_zone2_id', x),
                        lambda x: int(x)
                    )

                    if read_result & ReadLinesResult.STATE_UPDATED:
                        # At least one line evaluator read data and updated state.
                        state_updated = True

                    if read_result & ReadLinesResult.INCOMPLETE:
                        # At least one line evaluator didn't have enough lines.
                        break

                    if read_result == ReadLinesResult.IGNORED:
                        # All evaluators ignored the line; remove it.
                        self._read_lines.read_next_line()
                        self._read_lines.consume_read_lines()

                if state_updated:
                    await self._async_notify_device_state_updated()
            except Exception as ex:
                create_task(self.async_disconnect())
                exception = ex
                break

        self._read_loop_finished.set()
        self._reader = None
        await self._async_notify_disconnected()

        if exception is not None:
            raise RuntimeError("Error in reader loop") from exception

    async def _async_notify_disconnected(
        self
    ):
        await self._async_on_disconnected()

    async def _async_notify_device_state_updated(
        self
    ):
        await self._async_on_device_state_updated()

    async def _async_send_command(
        self,
        command: str
    ) -> None:
        """Sends given command to the server. Automatically appends
            CR to the command string."""
        self._writer.write(command + '\n')
        await self._writer.drain()

    async def async_set_power_command(self, power_command: PowerCommand):
        power_command_string: str = 'on' if power_command == PowerCommand.ON else 'off'
        await self._async_send_command(f'ssp.power.{power_command_string}')

    async def async_request_zones(self):
        await self._async_send_command('ssp.zones.list')

    async def async_set_mute(self, mute: bool):
        mute_command: str = 'on' if mute else 'off'
        await self._async_send_command(f'ssp.mute.{mute_command}')
    
    async def async_toggle_mute(self):
        await self._async_send_command(f'ssp.mute.toggle')

    async def async_set_volume(self, volume_db: Decimal):
        await self._async_send_command(f'ssp.vol.[{volume_db}]')

    async def async_set_input_id(self, input_id: int):
        await self._async_send_command(f'ssp.input.[{input_id}]')

    async def async_set_input_zone2_id(self, input_zone2_id: int):
        await self._async_send_command(f'ssp.inputZone2.[{input_zone2_id}]')

    async def async_set_preset_id(self, preset_id: int):
        await self._async_send_command(f'ssp.preset.[{preset_id}]')

    def _eval__line(
        self,
        expected_tokens: list(str),
        continue_fn,
    ) -> ReadLinesResult:
        if self._read_lines.has_next_line():
            line: TokenizedLineReader = self._read_lines.read_next_line()
            if line.pop_next_tokens_if_equal(expected=expected_tokens):
                read_result: ReadLinesResult = continue_fn(line)
                if read_result & ReadLinesResult.COMPLETE:
                    self._read_lines.consume_read_lines()
                else:
                    self._read_lines.reset_read_lines()
                return read_result
            self._read_lines.reset_read_lines()
            return ReadLinesResult.IGNORED
        return ReadLinesResult.INCOMPLETE

    def _eval_keepalive(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        self._keepalive_received = True
        return ReadLinesResult.COMPLETE

    def _eval__single_bracket_field(
        self,
        expected_tokens: list(str),
        set_fn,
        convert_fn
    ) -> ReadLinesResult:
        def parse_bracket_field(line: TokenizedLineReader):
            bracket_fields: list(str) = line.pop_next_token()
            if type(bracket_fields) is list:
                set_fn(convert_fn(bracket_fields[0]))
                return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
            return ReadLinesResult.IGNORED

        return self._eval__line(
            expected_tokens=expected_tokens,
            continue_fn=parse_bracket_field
        )

    def _eval_mute(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        if line.pop_next_token_if_equal('on'):
            self._device_state.mute = True
        elif line.pop_next_token_if_equal('off'):
            self._device_state.mute = False
        else:
            return ReadLinesResult.IGNORED
        return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED

    def _eval_power_command(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        if line.pop_next_token_if_equal('on'):
            self._device_state.power_command = PowerCommand.ON
        elif line.pop_next_token_if_equal('off'):
            self._device_state.power_command = PowerCommand.OFF
        else:
            return ReadLinesResult.IGNORED
        return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED

    def _eval_processor_state(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        bracket_fields: list(str) = line.pop_next_token()
        if type(bracket_fields) is list:
            if bracket_fields[0] == '0':
                self._device_state.processor_state = ProcessorState.OFF
            elif bracket_fields[0] == '1':
                self._device_state.processor_state = ProcessorState.INITIALIZING \
                    if self._device_state.power_command == PowerCommand.ON \
                    else ProcessorState.SHUTTING_DOWN
            elif bracket_fields[0] == '2':
                self._device_state.processor_state = ProcessorState.ON
            else:
                return ReadLinesResult.IGNORED
            return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
        return ReadLinesResult.IGNORED

    def _eval_inputs(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        new_inputs: list(Input) = []
        while self._read_lines.has_next_line():
            line = self._read_lines.read_next_line()
            if line.pop_next_tokens_if_equal(['ssp', 'input', 'list']):
                bracket_fields: list(str) = line.pop_next_token()
                if type(bracket_fields) is list:
                    input = Input(
                        name=bracket_fields[0].strip('"'),
                        id=int(bracket_fields[1]),
                        video_in_id=VideoInputID(
                            int(bracket_fields[2])),
                        audio_in_id=AudioInputID(
                            int(bracket_fields[3])),
                        audio_zone2_in_id=AudioZone2InputID(
                            int(bracket_fields[4])),
                        delay_ms=Decimal(bracket_fields[6])
                    )
                    new_inputs.append(input)
                else:
                    return ReadLinesResult.IGNORED
            elif line.pop_next_tokens_if_equal(['ssp', 'input', 'end']):
                # set input list
                self._device_state.inputs = new_inputs
                return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
            else:
                return ReadLinesResult.IGNORED
        return ReadLinesResult.INCOMPLETE

    def _eval_zones(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        new_zones: list(Zone) = []
        while self._read_lines.has_next_line():
            line = self._read_lines.read_next_line()
            if line.pop_next_tokens_if_equal(['ssp', 'zones', 'list']):
                bracket_fields: list(str) = line.pop_next_token()
                if type(bracket_fields) is list:
                    zone = Zone(
                        id=int(bracket_fields[0]),
                        name=bracket_fields[1].strip('"'),
                        zone_layout_type=ZoneLayoutType(
                            int(bracket_fields[2])),
                        zone_type=ZoneType(
                            int(bracket_fields[3])),
                        use_zone2_source=bool(int(bracket_fields[4])),
                        volume_db=Decimal(bracket_fields[5]),
                        delay_ms=Decimal(bracket_fields[6]),
                        mute=bool(int(bracket_fields[10]))
                    )
                    new_zones.append(zone)
                else:
                    return ReadLinesResult.IGNORED
            elif line.pop_next_tokens_if_equal(['ssp', 'zones', 'end']):
                # set input list
                self._device_state.zones = new_zones
                return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
            else:
                return ReadLinesResult.IGNORED
        return ReadLinesResult.INCOMPLETE

    def _parse_audio_zone_ids(self, bracket_field: str):
        bracket_field_token = bracket_field.strip('"["').strip('"]"')
        bracket_field_tokens: list[str] = []
        if len(bracket_field_token) > 0:
            bracket_field_tokens = bracket_field_token.split('","')
        return list(map(lambda x: int(x), bracket_field_tokens))

    def _eval_presets(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        new_presets: list(Preset) = []
        while self._read_lines.has_next_line():
            line = self._read_lines.read_next_line()
            if line.pop_next_tokens_if_equal(['ssp', 'preset', 'list']):
                bracket_fields: list(str) = line.pop_next_token()
                if type(bracket_fields) is list:
                    preset = Preset(
                        name=bracket_fields[0].strip('"'),
                        id=int(bracket_fields[1]),
                        audio_zone_ids=self._parse_audio_zone_ids(
                            bracket_fields[2]),
                        sphereaudio_theater_enabled=bool(
                            int(bracket_fields[3]))
                    )
                    new_presets.append(preset)
                else:
                    return ReadLinesResult.IGNORED
            elif line.pop_next_tokens_if_equal(['ssp', 'preset', 'end']):
                # set preset list
                self._device_state.presets = new_presets
                return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
            else:
                return ReadLinesResult.IGNORED
        return ReadLinesResult.INCOMPLETE
