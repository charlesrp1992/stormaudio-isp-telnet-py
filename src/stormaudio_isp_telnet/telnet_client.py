"""Classes for communicating with the Storm Audio ISP series sound processors"""

from __future__ import annotations
from async_timeout import timeout
from asyncio import create_task, Event, sleep, Task, TimeoutError
from decimal import *
from enum import IntFlag, auto

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
        self.inputs: list[Input] = None
        self.input_id: int = None
        self.input_zone2_id: int = None
        self.zones: list[Zone] = None
        self.presets: list[Preset] = None
        self.preset_id: int = None
        # Theater audio controls
        self.dim: bool = None
        self.bass: int = None
        self.treble: int = None
        self.brightness: int = None
        self.center_enhance: int = None
        self.surround_enhance: int = None
        self.lfe_enhance: int = None
        self.loudness: int = None
        self.lipsync: int = None
        self.surround_mode: int = None
        self.allowed_mode: int = None
        self.surround_modes: list[SurroundMode] = None
        self.drc: str = None
        self.dialog_control_available: bool = None
        self.dialog_control: int = None
        self.dialog_norm: bool = None
        self.dolby_mode: int = None
        self.storm_xt: bool = None
        self.lfe_dim: bool = None
        # Stream/HDMI info
        self.sample_rate: str = None
        self.stream_type: str = None
        self.channel_format: str = None
        self.firmware_version: str = None
        self.hdmi1_timing: str = None
        self.hdmi1_hdr: str = None
        self.hdmi2_timing: str = None
        self.hdmi2_hdr: str = None
        # Triggers
        self.triggers: dict[int, bool] = {}
        self.trigger_names: list[str] = None


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
        mute: bool,
        eq_enabled: bool = False,
        bass: int = 0,
        treble: int = 0,
        loudness: int = 0,
        lipsync_ms: int = 0,
        binaural_mode: bool = False
    ):
        self.name: str = name
        self.id: int = id
        self.zone_layout_type: ZoneLayoutType = zone_layout_type
        self.zone_type: ZoneType = zone_type
        self.use_zone2_source: bool = use_zone2_source
        self.volume_db = volume_db
        self.delay_ms: Decimal = delay_ms
        self.mute: bool = mute
        self.eq_enabled: bool = eq_enabled
        self.bass: int = bass
        self.treble: int = treble
        self.loudness: int = loudness
        self.lipsync_ms: int = lipsync_ms
        self.binaural_mode: bool = binaural_mode


class Preset:
    def __init__(
        self,
        name: str,
        id: int,
        audio_zone_ids: list[int],
        sphereaudio_theater_enabled: bool
    ):
        self.name: str = name
        self.id: int = id
        self.audio_zone_ids: list[int] = audio_zone_ids
        self.sphereaudio_theater_enabled: bool = sphereaudio_theater_enabled


class SurroundMode:
    def __init__(
        self,
        id: int,
        name: str
    ):
        self.id: int = id
        self.name: str = name


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
        self._remaining_output: str = ''
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

                    # --- Zone property updates ---
                    read_result |= self._eval__zone_property(
                        'volume', 'volume_db', lambda x: Decimal(x)
                    )
                    read_result |= self._eval__zone_property(
                        'mute', 'mute', lambda x: bool(int(x))
                    )
                    read_result |= self._eval__zone_property(
                        'eq', 'eq_enabled', lambda x: bool(int(x))
                    )
                    read_result |= self._eval__zone_property(
                        'bass', 'bass', lambda x: int(x)
                    )
                    read_result |= self._eval__zone_property(
                        'treble', 'treble', lambda x: int(x)
                    )
                    read_result |= self._eval__zone_property(
                        'loudness', 'loudness', lambda x: int(x)
                    )
                    read_result |= self._eval__zone_property(
                        'lipsync', 'lipsync_ms', lambda x: int(x)
                    )
                    read_result |= self._eval__zone_property(
                        'mode', 'binaural_mode', lambda x: bool(int(x))
                    )
                    read_result |= self._eval__zone_property(
                        'useZone2', 'use_zone2_source', lambda x: bool(int(x))
                    )

                    # --- Theater audio controls ---
                    read_result |= self._eval__line(
                        ['ssp', 'dim'],
                        self._eval_dim
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'bass'],
                        lambda x: self._device_state.__setattr__('bass', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'treble'],
                        lambda x: self._device_state.__setattr__('treble', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'brightness'],
                        lambda x: self._device_state.__setattr__('brightness', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'c_en'],
                        lambda x: self._device_state.__setattr__('center_enhance', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 's_en'],
                        lambda x: self._device_state.__setattr__('surround_enhance', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'lfe_en'],
                        lambda x: self._device_state.__setattr__('lfe_enhance', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'loudness'],
                        lambda x: self._device_state.__setattr__('loudness', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'lipsync'],
                        lambda x: self._device_state.__setattr__('lipsync', x),
                        lambda x: int(x)
                    )

                    # Surround mode list (must come before single bracket field)
                    read_result |= self._eval__line(
                        ['ssp', 'surroundmode', 'start'],
                        self._eval_surround_modes
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'surroundmode'],
                        lambda x: self._device_state.__setattr__('surround_mode', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'allowedmode'],
                        lambda x: self._device_state.__setattr__('allowed_mode', x),
                        lambda x: int(x)
                    )

                    read_result |= self._eval__line(
                        ['ssp', 'drc'],
                        self._eval_drc
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'dialogcontrol'],
                        self._eval_dialog_control
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'dialognorm'],
                        self._eval_dialog_norm
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'dolbymode'],
                        lambda x: self._device_state.__setattr__('dolby_mode', x),
                        lambda x: int(x)
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'stormxt'],
                        self._eval_storm_xt
                    )
                    read_result |= self._eval__line(
                        ['ssp', 'lfedim'],
                        self._eval_lfe_dim
                    )

                    # --- Stream/HDMI info ---
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'fs'],
                        lambda x: self._device_state.__setattr__('sample_rate', x),
                        lambda x: x.strip('"')
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'stream'],
                        lambda x: self._device_state.__setattr__('stream_type', x),
                        lambda x: x.strip('"')
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'format'],
                        lambda x: self._device_state.__setattr__('channel_format', x),
                        lambda x: x.strip('"')
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'version'],
                        lambda x: self._device_state.__setattr__('firmware_version', x),
                        lambda x: x.strip('"')
                    )

                    # HDMI info
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'hdmi1', 'timing'],
                        lambda x: self._device_state.__setattr__('hdmi1_timing', x),
                        lambda x: x.strip('"')
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'hdmi1', 'hdr'],
                        lambda x: self._device_state.__setattr__('hdmi1_hdr', x),
                        lambda x: x.strip('"')
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'hdmi2', 'timing'],
                        lambda x: self._device_state.__setattr__('hdmi2_timing', x),
                        lambda x: x.strip('"')
                    )
                    read_result |= self._eval__single_bracket_field(
                        ['ssp', 'hdmi2', 'hdr'],
                        lambda x: self._device_state.__setattr__('hdmi2_hdr', x),
                        lambda x: x.strip('"')
                    )

                    # --- Triggers ---
                    read_result |= self._eval__line(
                        ['ssp', 'trigger', 'start'],
                        self._eval_trigger_list
                    )
                    for trig_id in range(1, 13):
                        read_result |= self._eval__line(
                            ['ssp', f'trig{trig_id}'],
                            lambda line, tid=trig_id: self._eval_trigger_state(line, tid)
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

    # ---- Power / basic commands ----

    async def async_set_power_command(self, power_command: PowerCommand):
        power_command_string: str = 'on' if power_command == PowerCommand.ON else 'off'
        await self._async_send_command(f'ssp.power.{power_command_string}')

    async def async_request_zones(self):
        await self._async_send_command('ssp.zones.list')

    async def async_set_mute(self, mute: bool):
        mute_command: str = 'on' if mute else 'off'
        await self._async_send_command(f'ssp.mute.{mute_command}')

    async def async_toggle_mute(self):
        await self._async_send_command('ssp.mute.toggle')

    async def async_set_volume(self, volume_db: Decimal):
        await self._async_send_command(f'ssp.vol.[{volume_db}]')

    async def async_volume_up(self):
        await self._async_send_command('ssp.vol.up')

    async def async_volume_down(self):
        await self._async_send_command('ssp.vol.down')

    async def async_set_input_id(self, input_id: int):
        await self._async_send_command(f'ssp.input.[{input_id}]')

    async def async_set_input_zone2_id(self, input_zone2_id: int):
        await self._async_send_command(f'ssp.inputZone2.[{input_zone2_id}]')

    async def async_set_preset_id(self, preset_id: int):
        await self._async_send_command(f'ssp.preset.[{preset_id}]')

    # ---- Zone commands ----

    async def async_set_zone_volume(self, zone_id: int, volume_db: Decimal):
        await self._async_send_command(f'ssp.zones.volume.[{zone_id}, {volume_db}]')

    async def async_zone_volume_up(self, zone_id: int):
        await self._async_send_command(f'ssp.zones.volume.up.[{zone_id}]')

    async def async_zone_volume_down(self, zone_id: int):
        await self._async_send_command(f'ssp.zones.volume.down.[{zone_id}]')

    async def async_set_zone_mute(self, zone_id: int, mute: bool):
        await self._async_send_command(f'ssp.zones.mute.[{zone_id}, {1 if mute else 0}]')

    async def async_toggle_zone_mute(self, zone_id: int):
        await self._async_send_command(f'ssp.zones.mute.toggle.[{zone_id}]')

    async def async_set_zone_eq(self, zone_id: int, enabled: bool):
        await self._async_send_command(f'ssp.zones.eq.[{zone_id}, {1 if enabled else 0}]')

    async def async_toggle_zone_eq(self, zone_id: int):
        await self._async_send_command(f'ssp.zones.eq.toggle.[{zone_id}]')

    async def async_set_zone_bass(self, zone_id: int, value: int):
        clamped = max(-6, min(6, value))
        await self._async_send_command(f'ssp.zones.bass.[{zone_id}, {clamped}]')

    async def async_set_zone_treble(self, zone_id: int, value: int):
        clamped = max(-6, min(6, value))
        await self._async_send_command(f'ssp.zones.treble.[{zone_id}, {clamped}]')

    async def async_set_zone_loudness(self, zone_id: int, value: int):
        clamped = max(0, min(3, value))
        await self._async_send_command(f'ssp.zones.loudness.[{zone_id}, {clamped}]')

    async def async_set_zone_lipsync(self, zone_id: int, ms: int):
        await self._async_send_command(f'ssp.zones.lipsync.[{zone_id}, {ms}]')

    async def async_set_zone_use_zone2(self, zone_id: int, follow: bool):
        await self._async_send_command(f'ssp.zones.useZone2.[{zone_id}, {1 if follow else 0}]')

    async def async_toggle_zone_use_zone2(self, zone_id: int):
        await self._async_send_command(f'ssp.zones.useZone2.toggle.[{zone_id}]')

    async def async_set_zone_binaural(self, zone_id: int, enabled: bool):
        await self._async_send_command(f'ssp.zones.mode.[{zone_id}, {1 if enabled else 0}]')

    async def async_toggle_zone_binaural(self, zone_id: int):
        await self._async_send_command(f'ssp.zones.mode.toggle.[{zone_id}]')

    # ---- Theater audio controls ----

    async def async_set_dim(self, enabled: bool):
        await self._async_send_command(f'ssp.dim.{"on" if enabled else "off"}')

    async def async_toggle_dim(self):
        await self._async_send_command('ssp.dim.toggle')

    async def async_set_bass(self, value: int):
        clamped = max(-6, min(6, value))
        await self._async_send_command(f'ssp.bass.[{clamped}]')

    async def async_set_treble(self, value: int):
        clamped = max(-6, min(6, value))
        await self._async_send_command(f'ssp.treble.[{clamped}]')

    async def async_set_brightness(self, value: int):
        clamped = max(-6, min(6, value))
        await self._async_send_command(f'ssp.brightness.[{clamped}]')

    async def async_set_center_enhance(self, value: int):
        clamped = max(-6, min(6, value))
        await self._async_send_command(f'ssp.c_en.[{clamped}]')

    async def async_set_surround_enhance(self, value: int):
        clamped = max(-6, min(6, value))
        await self._async_send_command(f'ssp.s_en.[{clamped}]')

    async def async_set_lfe_enhance(self, value: int):
        clamped = max(-6, min(6, value))
        await self._async_send_command(f'ssp.lfe_en.[{clamped}]')

    async def async_set_loudness(self, value: int):
        clamped = max(0, min(3, value))
        await self._async_send_command(f'ssp.loudness.[{clamped}]')

    async def async_set_lipsync(self, value: int):
        await self._async_send_command(f'ssp.lipsync.[{value}]')

    async def async_set_surround_mode(self, mode_id: int):
        await self._async_send_command(f'ssp.surroundmode.[{mode_id}]')

    async def async_request_surround_modes(self):
        await self._async_send_command('ssp.surroundmode.list')

    async def async_set_drc(self, mode: str):
        """Set DRC mode: 'on', 'off', or 'auto'."""
        await self._async_send_command(f'ssp.drc.{mode}')

    async def async_set_dialog_control(self, value: int):
        clamped = max(0, min(6, value))
        await self._async_send_command(f'ssp.dialogcontrol.[{clamped}]')

    async def async_set_dialog_norm(self, enabled: bool):
        await self._async_send_command(f'ssp.dialognorm.{"on" if enabled else "off"}')

    async def async_set_dolby_mode(self, mode_id: int):
        await self._async_send_command(f'ssp.dolbymode.[{mode_id}]')

    async def async_set_storm_xt(self, enabled: bool):
        await self._async_send_command(f'ssp.stormxt.{"on" if enabled else "off"}')

    async def async_set_lfe_dim(self, enabled: bool):
        await self._async_send_command(f'ssp.lfedim.{"on" if enabled else "off"}')

    # ---- Stream/HDMI info queries ----

    async def async_request_stream_info(self):
        await self._async_send_command('ssp.fs')
        await self._async_send_command('ssp.stream')
        await self._async_send_command('ssp.format')

    async def async_request_firmware_version(self):
        await self._async_send_command('ssp.version')

    async def async_request_hdmi_info(self, hdmi_num: int):
        await self._async_send_command(f'ssp.hdmi{hdmi_num}.timing')
        await self._async_send_command(f'ssp.hdmi{hdmi_num}.hdr')

    # ---- Trigger commands ----

    async def async_set_trigger(self, trigger_id: int, enabled: bool):
        await self._async_send_command(f'ssp.trig{trigger_id}.{"on" if enabled else "off"}')

    async def async_toggle_trigger(self, trigger_id: int):
        await self._async_send_command(f'ssp.trig{trigger_id}.toggle')

    async def async_request_trigger_list(self):
        await self._async_send_command('ssp.trigger.list')

    # ---- Parsing infrastructure ----

    def _eval__line(
        self,
        expected_tokens: list[str],
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
        expected_tokens: list[str],
        set_fn,
        convert_fn
    ) -> ReadLinesResult:
        def parse_bracket_field(line: TokenizedLineReader):
            bracket_fields: list[str] = line.pop_next_token()
            if type(bracket_fields) is list and len(bracket_fields) >= 1:
                try:
                    set_fn(convert_fn(bracket_fields[0]))
                    return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
                except Exception:
                    return ReadLinesResult.IGNORED
            return ReadLinesResult.IGNORED

        return self._eval__line(
            expected_tokens=expected_tokens,
            continue_fn=parse_bracket_field
        )

    def _eval__zone_property(
        self,
        property_token: str,
        zone_attr: str,
        convert_fn
    ) -> ReadLinesResult:
        """Parse a zone property update like ssp.zones.volume.[ID, value]."""
        def parse_zone_prop(line: TokenizedLineReader):
            bracket_fields: list[str] = line.pop_next_token()
            if type(bracket_fields) is list and len(bracket_fields) >= 2:
                try:
                    zone_id = int(bracket_fields[0])
                    value = convert_fn(bracket_fields[1])
                except Exception:
                    return ReadLinesResult.IGNORED
                if self._device_state.zones is not None:
                    for zone in self._device_state.zones:
                        if zone.id == zone_id:
                            setattr(zone, zone_attr, value)
                            return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
            return ReadLinesResult.IGNORED

        return self._eval__line(
            expected_tokens=['ssp', 'zones', property_token],
            continue_fn=parse_zone_prop
        )

    # ---- Specific evaluators ----

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
        bracket_fields: list[str] = line.pop_next_token()
        if type(bracket_fields) is list and len(bracket_fields) >= 1:
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
        new_inputs: list[Input] = []
        while self._read_lines.has_next_line():
            line = self._read_lines.read_next_line()
            if line.pop_next_tokens_if_equal(['ssp', 'input', 'list']):
                bracket_fields: list[str] = line.pop_next_token()
                if type(bracket_fields) is list and len(bracket_fields) >= 7:
                    try:
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
                    except Exception:
                        return ReadLinesResult.IGNORED
                else:
                    return ReadLinesResult.IGNORED
            elif line.pop_next_tokens_if_equal(['ssp', 'input', 'end']):
                self._device_state.inputs = new_inputs
                return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
            else:
                return ReadLinesResult.IGNORED
        return ReadLinesResult.INCOMPLETE

    def _eval_zones(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        new_zones: list[Zone] = []
        while self._read_lines.has_next_line():
            line = self._read_lines.read_next_line()
            if line.pop_next_tokens_if_equal(['ssp', 'zones', 'list']):
                bracket_fields: list[str] = line.pop_next_token()
                if type(bracket_fields) is list and len(bracket_fields) >= 11:
                    try:
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
                    except Exception:
                        return ReadLinesResult.IGNORED
                else:
                    return ReadLinesResult.IGNORED
            elif line.pop_next_tokens_if_equal(['ssp', 'zones', 'end']):
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
        new_presets: list[Preset] = []
        while self._read_lines.has_next_line():
            line = self._read_lines.read_next_line()
            if line.pop_next_tokens_if_equal(['ssp', 'preset', 'list']):
                bracket_fields: list[str] = line.pop_next_token()
                if type(bracket_fields) is list and len(bracket_fields) >= 4:
                    try:
                        preset = Preset(
                            name=bracket_fields[0].strip('"'),
                            id=int(bracket_fields[1]),
                            audio_zone_ids=self._parse_audio_zone_ids(
                                bracket_fields[2]),
                            sphereaudio_theater_enabled=bool(
                                int(bracket_fields[3]))
                        )
                        new_presets.append(preset)
                    except Exception:
                        return ReadLinesResult.IGNORED
                else:
                    return ReadLinesResult.IGNORED
            elif line.pop_next_tokens_if_equal(['ssp', 'preset', 'end']):
                self._device_state.presets = new_presets
                return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
            else:
                return ReadLinesResult.IGNORED
        return ReadLinesResult.INCOMPLETE

    def _eval_surround_modes(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        new_modes: list[SurroundMode] = []
        while self._read_lines.has_next_line():
            line = self._read_lines.read_next_line()
            if line.pop_next_tokens_if_equal(['ssp', 'surroundmode', 'list']):
                bracket_fields: list[str] = line.pop_next_token()
                if type(bracket_fields) is list and len(bracket_fields) >= 2:
                    try:
                        mode = SurroundMode(
                            name=bracket_fields[0].strip('"'),
                            id=int(bracket_fields[1])
                        )
                        new_modes.append(mode)
                    except Exception:
                        return ReadLinesResult.IGNORED
                else:
                    return ReadLinesResult.IGNORED
            elif line.pop_next_tokens_if_equal(['ssp', 'surroundmode', 'end']):
                self._device_state.surround_modes = new_modes
                return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
            else:
                return ReadLinesResult.IGNORED
        return ReadLinesResult.INCOMPLETE

    def _eval_dim(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        if line.pop_next_token_if_equal('on'):
            self._device_state.dim = True
        elif line.pop_next_token_if_equal('off'):
            self._device_state.dim = False
        else:
            return ReadLinesResult.IGNORED
        return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED

    def _eval_drc(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        if line.pop_next_token_if_equal('on'):
            self._device_state.drc = 'on'
        elif line.pop_next_token_if_equal('off'):
            self._device_state.drc = 'off'
        elif line.pop_next_token_if_equal('auto'):
            self._device_state.drc = 'auto'
        else:
            return ReadLinesResult.IGNORED
        return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED

    def _eval_dialog_control(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        bracket_fields: list[str] = line.pop_next_token()
        if type(bracket_fields) is list:
            if len(bracket_fields) >= 2:
                try:
                    self._device_state.dialog_control_available = bool(int(bracket_fields[0]))
                    self._device_state.dialog_control = int(bracket_fields[1])
                    return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
                except Exception:
                    return ReadLinesResult.IGNORED
            elif len(bracket_fields) >= 1:
                try:
                    self._device_state.dialog_control = int(bracket_fields[0])
                    return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
                except Exception:
                    return ReadLinesResult.IGNORED
        return ReadLinesResult.IGNORED

    def _eval_dialog_norm(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        if line.pop_next_token_if_equal('on'):
            self._device_state.dialog_norm = True
        elif line.pop_next_token_if_equal('off'):
            self._device_state.dialog_norm = False
        else:
            return ReadLinesResult.IGNORED
        return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED

    def _eval_storm_xt(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        if line.pop_next_token_if_equal('on'):
            self._device_state.storm_xt = True
        elif line.pop_next_token_if_equal('off'):
            self._device_state.storm_xt = False
        else:
            return ReadLinesResult.IGNORED
        return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED

    def _eval_lfe_dim(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        if line.pop_next_token_if_equal('on'):
            self._device_state.lfe_dim = True
        elif line.pop_next_token_if_equal('off'):
            self._device_state.lfe_dim = False
        else:
            return ReadLinesResult.IGNORED
        return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED

    def _eval_trigger_list(
        self,
        line: TokenizedLineReader
    ) -> ReadLinesResult:
        new_names: list[str] = []
        while self._read_lines.has_next_line():
            line = self._read_lines.read_next_line()
            if line.pop_next_tokens_if_equal(['ssp', 'trigger', 'list']):
                bracket_fields: list[str] = line.pop_next_token()
                if type(bracket_fields) is list and len(bracket_fields) >= 1:
                    new_names.append(bracket_fields[0].strip('"'))
                else:
                    return ReadLinesResult.IGNORED
            elif line.pop_next_tokens_if_equal(['ssp', 'trigger', 'end']):
                self._device_state.trigger_names = new_names
                return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
            else:
                return ReadLinesResult.IGNORED
        return ReadLinesResult.INCOMPLETE

    def _eval_trigger_state(
        self,
        line: TokenizedLineReader,
        trigger_id: int
    ) -> ReadLinesResult:
        if line.pop_next_token_if_equal('on'):
            self._device_state.triggers[trigger_id] = True
        elif line.pop_next_token_if_equal('off'):
            self._device_state.triggers[trigger_id] = False
        else:
            return ReadLinesResult.IGNORED
        return ReadLinesResult.COMPLETE | ReadLinesResult.STATE_UPDATED
