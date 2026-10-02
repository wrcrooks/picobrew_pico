import json
import random
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .protocol import (MACHINE_LOCATION_CODES, ZYMATIC_CLEAN_USER, PicoServerClient, ProtocolError,
                       RecipeStep, ZSeriesClient, ZymaticClient, zseries_step)

# machine_type is what the server's /devices form expects; protocol picks the device class.
MODELS = {
    'pico_s': {'label': 'Pico S', 'machine_type': 'PicoBrew', 'firmware': '0.1.34', 'protocol': 'pico'},
    'pico_pro': {'label': 'Pico Pro', 'machine_type': 'PicoBrew', 'firmware': '0.1.34', 'protocol': 'pico'},
    'zymatic': {'label': 'Zymatic', 'machine_type': 'Zymatic', 'firmware': '0.1.14', 'protocol': 'zymatic'},
    'zseries': {'label': 'Z Series', 'machine_type': 'ZSeries', 'firmware': '0.0.116', 'protocol': 'zseries'},
}

# picoChangeState codes
STATE_READY = 2
STATE_BREWING = 3
STATE_SOUS_VIDE = 4
STATE_RINSE = 6
STATE_DEEP_CLEAN = 7

# Pico getSession / log sesType codes
SES_BREW = 0
SES_DEEP_CLEAN = 1
SES_SOUS_VIDE = 2

# Z-Series session types (ZSessionType in app/main/session_parser.py)
Z_RINSE = 0
Z_CLEAN = 1
Z_BEER = 6

AMBIENT_F = 70.0
MAX_WORT_F = 209.0
HEAT_RATE_F_PER_MIN = 3.0
COOL_COEFF_PER_MIN = 0.02
SUBSTEP_SECONDS = 5.0

# Approximations of the built-in maintenance programs: the server only cares about the
# session type and the step/event names it is sent, not the exact real-firmware sequence.
DEEP_CLEAN_STEPS = [
    RecipeStep('Preparing To Clean', 'Prime', 0, 2, 0),
    RecipeStep('Heating', 'PassThru', 140, 0, 0),
    RecipeStep('Cleaning Mash', 'Mash', 140, 10, 2),
    RecipeStep('Cleaning Adjunct 1', 'Adjunct1', 140, 3, 1),
    RecipeStep('Cleaning Adjunct 2', 'Adjunct2', 140, 3, 1),
    RecipeStep('Cleaning Adjunct 3', 'Adjunct3', 140, 3, 1),
    RecipeStep('Cleaning Adjunct 4', 'Adjunct4', 140, 3, 1),
    RecipeStep('Final Rinse', 'PassThru', 0, 5, 2),
]

RINSE_STEPS = [
    RecipeStep('Rinse', 'PassThru', 0, 2, 0),
    RecipeStep('Rinse Mash', 'Mash', 0, 1, 1),
    RecipeStep('Rinse Adjuncts', 'Adjunct1', 0, 1, 1),
]

Z_CLEAN_STEPS = [
    RecipeStep('Heat Water', 'PassThru', 140, 0, 0),
    RecipeStep('Clean Mash', 'Mash', 140, 10, 2),
    RecipeStep('Clean Adjuncts', 'Adjunct1', 140, 5, 2),
    RecipeStep('Rinse', 'PassThru', 0, 5, 2),
]

Z_RINSE_STEPS = [
    RecipeStep('Rinse', 'PassThru', 0, 2, 0),
    RecipeStep('Rinse Mash', 'Mash', 0, 1, 1),
]


def to_celsius(f):
    return round((f - 32) * 5 / 9, 2)


class CommandError(Exception):
    pass


class DeviceNotFound(LookupError):
    pass


class Program:
    def __init__(self, kind, label, steps, logged=True, **meta):
        self.kind = kind
        self.label = label
        self.steps = steps
        self.logged = logged      # False = the program isn't reported to the server at all
        self.meta = meta          # protocol-specific session details


class Device:
    """A simulated brewing machine: power, thermal model and step runner are shared; each
    subclass supplies its protocol through the hooks at the bottom of this class."""
    CLIENT = None
    LOG_INTERVAL_SIM_SECONDS = 10
    CAPABILITIES = ()

    def __init__(self, device_id, name, model, uid, firmware, server_url, speed=1.0, http=None,
                 tick_interval=1.0, extra=None):
        if model not in MODELS:
            raise CommandError(f'Unknown model {model!r}')
        self.tick_interval = tick_interval  # None = no background thread; caller drives tick()
        self.id = device_id
        self.name = name
        self.model = model
        self.uid = uid
        self.firmware = firmware
        self.server_url = server_url
        self.speed = speed
        self.extra = dict(extra or {})      # protocol-specific persisted settings
        self.on_config_change = None        # set by DeviceManager to persist extra settings
        self._http = http
        self.client = self.CLIENT(server_url, http=http)

        self.lock = threading.RLock()
        self.powered = False
        self._thread = None
        self._stop = threading.Event()

        self.registered = None
        self.firmware_update_available = None
        self.needs_cleaning = False
        self.recipe = None
        self.error_code = 0
        self.last_comm_error = None

        self.wort = AMBIENT_F
        self.therm = AMBIENT_F
        self.sim_clock = 0.0
        self.history = deque(maxlen=3000)
        self.events = deque(maxlen=60)

        self.program = None
        self.step_index = 0
        self.phase = None
        self.phase_elapsed = 0.0
        self.user_paused = False
        self._pending_event = None
        self._last_log_clock = None
        self._program_started_clock = 0.0

    # ---------- config ----------
    def config(self):
        return {'id': self.id, 'name': self.name, 'model': self.model, 'uid': self.uid,
                'firmware': self.firmware, 'server_url': self.server_url, 'speed': self.speed,
                'extra': dict(self.extra)}

    def update_config(self, name=None, firmware=None, server_url=None, speed=None):
        with self.lock:
            if name is not None:
                if not name.strip():
                    raise CommandError('Name is required')
                self.name = name.strip()
            if firmware is not None:
                self.firmware = firmware.strip()
            if speed is not None:
                speed = float(speed)
                if not 0.1 <= speed <= 600:
                    raise CommandError('Speed must be between 0.1x and 600x')
                self.speed = speed
            if server_url is not None and server_url.rstrip('/') != self.server_url:
                self.server_url = validate_server_url(server_url)
                self.client = self.CLIENT(self.server_url, http=self._http)

    def _save_extra(self, **values):
        self.extra.update(values)
        if self.on_config_change:
            self.on_config_change()

    # ---------- helpers ----------
    def _note(self, text, level='info'):
        self.events.appendleft({'time': time.time(), 'level': level, 'text': text})

    def _call(self, fn, *args, **kwargs):
        """Run a server call, recording (rather than raising) connectivity failures."""
        try:
            result = fn(*args, **kwargs)
            self.last_comm_error = None
            return result
        except ProtocolError as e:
            self.last_comm_error = str(e)
            self._note(str(e), 'error')
            raise CommandError(str(e)) from e

    def _require_power(self):
        if not self.powered:
            raise CommandError('Device is powered off')

    def _require_idle(self):
        self._require_power()
        if self.program:
            raise CommandError(f'Busy running {self.program.label}')

    @property
    def current_step(self):
        if self.program and self.step_index < len(self.program.steps):
            return self.program.steps[self.step_index]
        return None

    # ---------- power ----------
    def power_on(self):
        with self.lock:
            if not self.powered:
                self.powered = True
                self._stop = threading.Event()
                if self.tick_interval:
                    self._thread = threading.Thread(target=self._run_loop, args=(self._stop,),
                                                    name=f'sim-{self.id}', daemon=True)
                    self._thread.start()
                self._note('Powered on')
        self.handshake()

    def power_off(self):
        """Pulling the plug: nothing is sent to the server, so an open session stays open."""
        with self.lock:
            if self.program:
                self._note(f'Power lost during {self.program.label}', 'warn')
            self.program = None
            self.phase = None
            self.powered = False
            self.recipe = None
            self._stop.set()
            self._note('Powered off')

    # ---------- programs ----------
    def programs(self):
        """Built-in (non-recipe) programs this model offers: [{'key', 'label'}]."""
        return []

    def start_program(self, key, **params):
        if key not in {p['key'] for p in self.programs()}:
            raise CommandError(f'{MODELS[self.model]["label"]} has no {key!r} program')
        getattr(self, f'start_{key}')(**params)

    def start_brew(self):
        self._require_idle()
        if not self.recipe:
            raise CommandError(self.NO_RECIPE_MESSAGE)
        if not self.recipe.steps:
            raise CommandError('Recipe has no steps')
        self._start(self._brew_program())

    def _start(self, program, open_session=True):
        if open_session and program.logged:
            self._open_session(program)
        with self.lock:
            self.program = program
            self.user_paused = False
            self._program_started_clock = self.sim_clock
            self._last_log_clock = None
            self._enter_step(0)
        self._note(f'Started {program.label}')

    def pause(self):
        with self.lock:
            if not self.program:
                raise CommandError('Nothing is running')
            self.user_paused = True
        self._note('Paused')

    def resume(self):
        """Resumes a user pause, or continues past a recipe 'Pause' location step."""
        with self.lock:
            if not self.program:
                raise CommandError('Nothing is running')
            if self.phase == 'waiting':
                self.phase = 'holding'
                self.phase_elapsed = 0.0
                self._note(f'Continuing past "{self.current_step.name}"')
            self.user_paused = False
        self._note('Resumed')

    def skip_step(self):
        with self.lock:
            if not self.program:
                raise CommandError('Nothing is running')
            self._note(f'Skipped "{self.current_step.name}"')
            calls = self._next_step()
        self._send(calls)

    def abort(self):
        with self.lock:
            if not self.program:
                raise CommandError('Nothing is running')
            program = self.program
            calls = self._abort_calls(program)
            self.program = None
            self.phase = None
            self.user_paused = False
        self._note(f'Aborted {program.label}', 'warn')
        for fn, kwargs in calls:
            self._call(fn, **kwargs)
        self._after_abort()

    # ---------- faults ----------
    def report_error(self, code):
        self._require_power()
        code = int(code)
        self._report_error(code)
        with self.lock:
            self.error_code = code
        self._note(f'Reported error {code}', 'warn')

    def clear_error(self):
        with self.lock:
            self.error_code = 0
        self._note('Error cleared')

    def register_alias(self):
        self._call(self.client.register_alias, self.uid, self.name, MODELS[self.model]['machine_type'])
        self._note(f'Submitted alias "{self.name}" to the server\'s /devices page')

    # ---------- simulation ----------
    def _run_loop(self, stop):
        last = time.monotonic()
        while not stop.wait(self.tick_interval):
            now = time.monotonic()
            self.tick(now - last)
            last = now

    def tick(self, real_seconds):
        with self.lock:
            sim_seconds = real_seconds * (self.speed if self.program else 1.0)
            calls = []
            remaining = sim_seconds
            while remaining > 0:
                dt = min(SUBSTEP_SECONDS, remaining)
                remaining -= dt
                self.sim_clock += dt
                calls += self._advance(dt)
            if self.program:
                calls += self._maybe_log()
            step = self.current_step
            self.history.append({'t': round(self.sim_clock, 1), 'wort': round(self.wort, 1),
                                 'therm': round(self.therm, 1), 'step': step.name if step else None})
        self._send(calls)

    def _send(self, calls):
        for fn, kwargs in calls:
            try:
                self._call(fn, **kwargs)
            except CommandError:
                pass  # a real machine keeps brewing when the network drops

    def _target_temperature(self):
        step = self.current_step
        if not step or self.user_paused or self.phase == 'waiting' or step.temperature <= 0:
            return None
        return float(step.temperature)

    def _physics(self, dt):
        minutes = dt / 60.0
        target = self._target_temperature()
        if target is not None and self.wort < target - 0.3:
            self.wort = min(target, MAX_WORT_F, self.wort + HEAT_RATE_F_PER_MIN * minutes)
            therm_target = min(self.wort + 14, 215)
        elif target is not None and self.wort <= target + 0.5:
            self.wort += (target - self.wort) * min(1.0, minutes * 2)
            therm_target = target + 3
        else:
            self.wort -= (self.wort - AMBIENT_F) * min(1.0, COOL_COEFF_PER_MIN * minutes)
            therm_target = self.wort + 0.5
        self.therm += (therm_target - self.therm) * min(1.0, minutes * 3)

    def _advance(self, dt):
        self._physics(dt)
        step = self.current_step
        if not step or self.user_paused or self.phase == 'waiting':
            return []

        if self.phase == 'heating':
            target = self._target_temperature()
            if target is None or self.wort >= target - 1:
                self.phase, self.phase_elapsed = 'holding', 0.0
            return []

        self.phase_elapsed += dt
        if self.phase == 'holding' and self.phase_elapsed >= step.step_time * 60:
            self.phase, self.phase_elapsed = 'draining', 0.0
        if self.phase == 'draining' and self.phase_elapsed >= step.drain_time * 60:
            return self._next_step()
        return []

    def _enter_step(self, index):
        self.step_index = index
        self.phase = 'waiting' if self.current_step.location == 'Pause' else 'heating'
        self.phase_elapsed = 0.0
        self._pending_event = self.current_step.name
        if self.phase == 'waiting':
            self._note(f'Paused at "{self.current_step.name}" - press Resume to continue', 'warn')

    def _next_step(self):
        """Advance to the next step, or finish the program. Returns server calls to make."""
        program = self.program
        if self.step_index + 1 < len(program.steps):
            calls = self._log_calls() if self._pending_event else []
            self._enter_step(self.step_index + 1)
            return calls + self._log_calls()

        calls = self._finish_calls(program)
        self._note(f'{program.label} complete')
        self.program = None
        self.phase = None
        return calls

    def _step_seconds_left(self):
        step = self.current_step
        if not step:
            return 0
        if self.phase == 'holding':
            return max(0, int((step.step_time + step.drain_time) * 60 - self.phase_elapsed))
        if self.phase == 'draining':
            return max(0, int(step.drain_time * 60 - self.phase_elapsed))
        return (step.step_time + step.drain_time) * 60

    def _time_left(self):
        if not self.current_step:
            return 0
        future = sum((s.step_time + s.drain_time) * 60 for s in self.program.steps[self.step_index + 1:])
        return max(0, int(self._step_seconds_left() + future))

    def _log_calls(self):
        if not self.program.logged:
            self._pending_event = None
            return []
        event, self._pending_event = self._pending_event, None
        self._last_log_clock = self.sim_clock
        return self._telemetry(event)

    def _maybe_log(self):
        due = (self._last_log_clock is None
               or self.sim_clock - self._last_log_clock >= self.LOG_INTERVAL_SIM_SECONDS)
        if self._pending_event or due:
            return self._log_calls()
        return []

    def _reading(self, value):
        return value + random.uniform(-0.3, 0.3)

    # ---------- UI ----------
    def snapshot(self, history_since=None):
        with self.lock:
            step = self.current_step
            program = None
            if self.program:
                program = {
                    'kind': self.program.kind,
                    'label': self.program.label,
                    'session': {k: v for k, v in self.program.meta.items() if isinstance(v, (str, int, float))},
                    'steps': [s.to_dict() for s in self.program.steps],
                    'step_index': self.step_index,
                    'phase': self.phase,
                    'phase_elapsed': round(self.phase_elapsed),
                    'time_left': self._time_left(),
                    'elapsed': round(self.sim_clock - self._program_started_clock),
                    'user_paused': self.user_paused,
                }
            history = list(self.history)
            if history_since is not None:
                history = [h for h in history if h['t'] > history_since]
            return {
                **self.config(),
                'model_label': MODELS[self.model]['label'],
                'machine_type': MODELS[self.model]['machine_type'],
                'protocol': MODELS[self.model]['protocol'],
                'capabilities': list(self.CAPABILITIES),
                'programs': self.programs(),
                'powered': self.powered,
                'registered': self.registered,
                'firmware_update_available': self.firmware_update_available,
                'needs_cleaning': self.needs_cleaning,
                'recipe': self.recipe.to_dict() if self.recipe else None,
                'program': program,
                'step': step.name if step else None,
                'wort': round(self.wort, 1),
                'therm': round(self.therm, 1),
                'error_code': self.error_code,
                'last_comm_error': self.last_comm_error,
                'sim_clock': round(self.sim_clock, 1),
                'history': history,
                'events': list(self.events),
                'traffic': list(self.client.traffic)[-150:],
                **self._snapshot_extra(),
            }

    # ---------- protocol hooks ----------
    NO_RECIPE_MESSAGE = 'Choose a recipe first'

    def handshake(self):
        raise NotImplementedError

    def _brew_program(self):
        raise NotImplementedError

    def _open_session(self, program):
        """Tell the server a logged program is starting; store its session ids in program.meta."""

    def _telemetry(self, event):
        """Server calls for one periodic log; event is the step that just started, if any."""
        return []

    def _finish_calls(self, program):
        return []

    def _abort_calls(self, program):
        return []

    def _after_abort(self):
        pass

    def _report_error(self, code):
        pass

    def _snapshot_extra(self):
        return {}


class PicoDevice(Device):
    CLIENT = PicoServerClient
    CAPABILITIES = ('paks', 'firmware_download', 'sous_vide')
    NO_RECIPE_MESSAGE = 'Insert a recognized PicoPak first'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.paks = []

    def handshake(self):
        """The boot sequence a Pico performs against the server."""
        self._require_power()
        self.registered = self._call(self.client.register, self.uid)
        self.firmware_update_available = self._call(self.client.check_firmware, self.uid, self.firmware)
        self.needs_cleaning = self._call(self.client.get_actions_needed, self.uid) == '7'
        self.paks = self._call(self.client.get_associated_paks, self.uid)
        self._note(f'Connected: {len(self.paks)} PicoPak(s) available'
                   + ('; firmware update available' if self.firmware_update_available else '')
                   + ('; deep clean required' if self.needs_cleaning else ''))

    def refresh_paks(self):
        self._require_power()
        self.paks = self._call(self.client.get_associated_paks, self.uid)
        self._note(f'{len(self.paks)} PicoPak(s) available')

    def insert_pak(self, rfid):
        self._require_idle()
        rfid = (rfid or '').strip()
        if not rfid:
            raise CommandError('RFID is required')
        recipe = self._call(self.client.get_recipe, self.uid, rfid)
        with self.lock:
            self.recipe = recipe
        if recipe is None:
            hint = ''
            if len(rfid) == 32 and all(c in '0123456789abcdefABCDEF' for c in rfid):
                hint = (" - that looks like a recipe's 32-character RecipeGUID; a Pico tag uses the recipe's "
                        "14-character Tag ID (set it in the recipe editor's Tag Programming section)")
            self._note(f'PicoPak {rfid} not recognized by server{hint}', 'warn')
        else:
            self._note(f'Loaded "{recipe.name}" ({len(recipe.steps)} steps)')
        return recipe

    def eject_pak(self):
        self._require_idle()
        with self.lock:
            self.recipe = None
        self._note('PicoPak removed')

    def programs(self):
        return [{'key': 'deep_clean', 'label': 'Deep clean'}, {'key': 'rinse', 'label': 'Rinse'}]

    def _brew_program(self):
        return Program('brew', f'Brew: {self.recipe.name}', list(self.recipe.steps), ses_type=SES_BREW,
                       ses_id=self.recipe.rfid, state=STATE_BREWING, complete_step='Brewing Complete')

    def start_deep_clean(self):
        self._require_idle()
        ses_id = self._call(self.client.get_session, self.uid, SES_DEEP_CLEAN)
        self._start(Program('deep_clean', 'Deep Clean', list(DEEP_CLEAN_STEPS), ses_type=SES_DEEP_CLEAN,
                            ses_id=ses_id, state=STATE_DEEP_CLEAN, complete_step='Deep Clean Complete'))
        self.needs_cleaning = False

    def start_rinse(self):
        self._require_idle()
        self._call(self.client.change_state, self.uid, STATE_RINSE)
        self._start(Program('rinse', 'Rinse', list(RINSE_STEPS), logged=False, state=STATE_RINSE,
                            complete_step='Rinse Complete'))

    def start_sous_vide(self, temperature, minutes):
        self._require_idle()
        temperature, minutes = int(temperature), int(minutes)
        if not 80 <= temperature <= 208:
            raise CommandError('Sous vide temperature must be 80-208 F')
        if not 1 <= minutes <= 48 * 60:
            raise CommandError('Sous vide time must be 1-2880 minutes')
        ses_id = self._call(self.client.get_session, self.uid, SES_SOUS_VIDE)
        steps = [RecipeStep('Heating', 'PassThru', temperature, 0, 0),
                 RecipeStep('Sous Vide', 'PassThru', temperature, minutes, 0)]
        self._start(Program('sous_vide', f'Sous Vide {temperature}F / {minutes} min', steps, ses_type=SES_SOUS_VIDE,
                            ses_id=ses_id, state=STATE_SOUS_VIDE, complete_step='Sous Vide Complete'))

    def _open_session(self, program):
        self._call(self.client.change_state, self.uid, program.meta['state'])

    def _log_kwargs(self, step_name, event, time_left):
        meta = self.program.meta
        return {'uid': self.uid, 'ses_id': meta['ses_id'], 'ses_type': meta['ses_type'],
                'wort': self._reading(self.wort), 'therm': self._reading(self.therm),
                'step': step_name, 'time_left': time_left, 'error': self.error_code, 'event': event}

    def _telemetry(self, event):
        return [(self.client.log, self._log_kwargs(self.current_step.name, event, self._time_left()))]

    def _finish_calls(self, program):
        calls = []
        if program.logged:
            step = program.meta['complete_step']   # the server closes the session on a step containing "complete"
            calls.append((self.client.log, self._log_kwargs(step, step, 0)))
        calls.append((self.client.change_state, {'uid': self.uid, 'state': STATE_READY}))
        return calls

    def _after_abort(self):
        self._call(self.client.change_state, self.uid, STATE_READY)
        # Returning to the main menu re-queries paks, which is also what makes the server
        # close out the abandoned session file.
        self.refresh_paks()

    def _report_error(self, code):
        self._call(self.client.error, self.uid, code, self.recipe.rfid if self.recipe else '')

    def check_firmware(self):
        self._require_power()
        self.firmware_update_available = self._call(self.client.check_firmware, self.uid, self.firmware)
        self._note('Firmware update available' if self.firmware_update_available else 'Firmware is up to date')

    def download_firmware(self):
        self._require_power()
        body = self._call(self.client.get_firmware, self.uid)
        if body.strip() == '#F#':
            self._note('Server has no firmware for this device (set its machine type on the server\'s '
                       '/devices page)', 'warn')
        else:
            self._note(f'Received firmware image ({len(body):,} bytes)')

    def _snapshot_extra(self):
        return {'paks': self.paks}


class MenuDevice(Device):
    """Machines that browse a server recipe menu (Zymatic, Z-Series) rather than scan tags."""
    CAPABILITIES = ('menu',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu = []

    def refresh_menu(self):
        self._require_power()
        self.menu = self._fetch_menu()
        self._note(f'{len(self.menu)} recipe(s) on the menu')

    def select_recipe(self, recipe_id):
        self._require_idle()
        entry = next((m for m in self.menu if m.id == str(recipe_id)), None)
        if entry is None:
            raise CommandError(f'Recipe {recipe_id!r} is not on the menu - refresh the menu')
        recipe = self._recipe_details(entry)
        with self.lock:
            self.recipe = recipe
        self._note(f'Selected "{recipe.name}" ({len(recipe.steps)} steps)')

    def _recipe_details(self, entry):
        return entry

    def _snapshot_extra(self):
        return {'menu': [m.menu_entry() for m in self.menu]}


class ZymaticDevice(MenuDevice):
    CLIENT = ZymaticClient

    @property
    def profile(self):
        return self.extra.get('profile_guid')

    def handshake(self):
        """Zymatic boot: firmware check, then sync the brew and cleaning menus for its profile."""
        self._require_power()
        if not self.profile:
            self._save_extra(profile_guid=self._call(self.client.user_setup, self.uid))
            self._note(f'Linked to server profile {self.profile}')
        self.firmware_update_available = self._call(self.client.firmware_check, self.uid, self.firmware)
        self.refresh_menu()
        self._call(self.client.check_sync, self.profile)
        self.registered = True

    def _fetch_menu(self):
        return (self._call(self.client.sync_user, self.profile, self.uid, 'Recipes')
                + self._call(self.client.sync_user, ZYMATIC_CLEAN_USER, self.uid, 'Cleaning'))

    def check_firmware(self):
        self._require_power()
        self.firmware_update_available = self._call(self.client.firmware_check, self.uid, self.firmware)
        self._note('Firmware update available' if self.firmware_update_available else 'Firmware is up to date')

    def _brew_program(self):
        verb = 'Clean' if self.recipe.group == 'Cleaning' else 'Brew'
        return Program('brew', f'{verb}: {self.recipe.name}', list(self.recipe.steps), recipe_id=self.recipe.id)

    def _open_session(self, program):
        session = self._call(self.client.start_session, self.profile, program.meta['recipe_id'], self.uid, self.firmware)
        if not session:
            raise CommandError('Server did not start a session')
        program.meta['session'] = session

    def _state(self):
        # Simulator convention for the undocumented 'state' field: 1 running, 2 paused.
        return 2 if self.user_paused or self.phase == 'waiting' else 1

    def _telemetry(self, event):
        session = self.program.meta['session']
        calls = []
        if event:
            calls.append((self.client.log_event, {'session': session, 'step_name': event, 'state': self._state()}))
        # 'step' is 8 integers for recovery; the simulator sends step index and elapsed minutes.
        recovery = '/'.join(map(str, [self.step_index, int(self.phase_elapsed // 60)] + [0] * 6))
        calls.append((self.client.log_temperatures, {
            'session': session, 'wort': self._reading(self.wort), 'heat1': self._reading(self.therm),
            'board': 95, 'heat2': self._reading(self.therm - 2), 'recovery': recovery, 'state': self._state()}))
        return calls

    def _finish_calls(self, program):
        return [(self.client.end_session, {'session': program.meta['session']})]

    def _abort_calls(self, program):
        return self._finish_calls(program)

    def _report_error(self, code):
        if self.program:
            self._call(self.client.session_error, self.uid, self.program.meta['session'], code)

    def _snapshot_extra(self):
        return {**super()._snapshot_extra(), 'profile_guid': self.profile}


class ZSeriesDevice(MenuDevice):
    CLIENT = ZSeriesClient
    CAPABILITIES = ('menu', 'resume')
    BOILER_TYPE = 1
    PROGRAM_ID = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resumable_session_id = -1
        self.dirty_sessions = None

    def handshake(self):
        """Z boot: report machine state (firmware, boiler) and get session stats back."""
        self._require_power()
        state = self._call(self.client.zstate, self.uid, self.firmware, self.BOILER_TYPE)
        stats = state.get('SessionStats') or {}
        self.registered = state.get('IsRegistered')
        self.firmware_update_available = not state.get('IsUpdated', True)
        self.dirty_sessions = stats.get('DirtySessionsSinceClean')
        self.needs_cleaning = (self.dirty_sessions or 0) >= 3
        self.resumable_session_id = stats.get('ResumableSessionID', -1)
        if state.get('ZBackendError'):
            self._note(f'Server reported ZBackendError {state["ZBackendError"]}', 'warn')
        if self.resumable_session_id not in (None, -1):
            self._note(f'Server has resumable session {self.resumable_session_id}', 'warn')
        self.refresh_menu()

    def check_firmware(self):
        self._require_power()
        state = self._call(self.client.zstate, self.uid, self.firmware, self.BOILER_TYPE)
        self.firmware_update_available = not state.get('IsUpdated', True)
        self._note('Firmware update available' if self.firmware_update_available else 'Firmware is up to date')

    def _fetch_menu(self):
        return self._call(self.client.recipe_list, self.uid)

    def _recipe_details(self, entry):
        return self._call(self.client.recipe, self.uid, entry.id)

    def programs(self):
        return [{'key': 'clean', 'label': 'Clean'}, {'key': 'rinse', 'label': 'Rinse'}]

    def _brew_program(self):
        return Program('brew', f'Brew: {self.recipe.name}', list(self.recipe.steps), session_type=Z_BEER,
                       session_name=self.recipe.name, recipe_id=int(self.recipe.id))

    def start_clean(self):
        self._require_idle()
        self._start(Program('clean', 'Clean', list(Z_CLEAN_STEPS), session_type=Z_CLEAN,
                            session_name='Clean', recipe_id=-1))

    def start_rinse(self):
        self._require_idle()
        self._start(Program('rinse', 'Rinse', list(Z_RINSE_STEPS), session_type=Z_RINSE,
                            session_name='Rinse', recipe_id=-1))

    def _session_body(self, program):
        return {'Name': program.meta['session_name'], 'SessionType': program.meta['session_type'],
                'DurationSec': round(self.sim_clock - self._program_started_clock), 'FirmwareVersion': self.firmware,
                'GroupSession': False, 'MaxTemp': to_celsius(self.wort), 'MaxTempAddedSec': 0, 'PressurePa': 0,
                'ZProgramId': self.PROGRAM_ID, 'RecipeID': program.meta['recipe_id']}

    def _open_session(self, program):
        created = self._call(self.client.create_session, self.uid, self._session_body(program))
        program.meta['session_id'] = created['ID']
        self.resumable_session_id = created['ID']

    def resume_session(self):
        """Pick up a session the server still has open (e.g. after power was cut mid-brew)."""
        self._require_idle()
        if self.resumable_session_id in (None, -1):
            raise CommandError('The server reported no resumable session at boot')
        resumed = self._call(self.client.resumable_session, self.uid, self.resumable_session_id)
        recipe = resumed['Recipe']
        steps = [zseries_step(s) for s in recipe['Steps']]
        # The server echoes the last logged SecondsRemaining as the first step's Time; the
        # simulator reports seconds left in the current step, so convert back to minutes.
        first = steps[0]
        steps[0] = RecipeStep(first.name, first.location, first.temperature,
                              max(0, round(first.step_time / 60) - first.drain_time), first.drain_time)
        program = Program('brew', f'Resumed: {recipe["Name"]}', steps, session_type=resumed['SessionType'],
                          session_name=recipe['Name'], recipe_id=recipe['ID'], session_id=resumed['SessionID'])
        self._start(program, open_session=False)

    def _pause_reason(self):
        if self.phase == 'waiting':
            return 1    # waiting for the user
        return 2 if self.user_paused else 0

    def _telemetry(self, event):
        step = self.current_step
        target = self._target_temperature()
        body = {
            'ZSessionID': self.program.meta['session_id'],
            'StepName': step.name,
            # Simulator convention: seconds left in the current step (see resume_session).
            'SecondsRemaining': self._step_seconds_left(),
            # The Z reports Celsius; the server converts to Fahrenheit.
            'TargetTemp': to_celsius(target if target is not None else self.wort),
            'AmbientTemp': to_celsius(AMBIENT_F),
            'DrainTemp': to_celsius(self._reading(self.wort - 2)),
            'WortTemp': to_celsius(self._reading(self.wort)),
            'ThermoBlockTemp': to_celsius(self._reading(self.therm)),
            'ValvePosition': MACHINE_LOCATION_CODES.get(step.location, 0),
            'DrainPumpOn': 1 if self.phase == 'draining' else 0,
            'KegPumpOn': 0 if self._pause_reason() else 1,
            'ErrorCode': self.error_code,
            'PauseReason': self._pause_reason(),
            'rssi': -55, 'netRecv': 0, 'netSend': 0, 'netWait': 0,
        }
        return [(self.client.log, {'token': self.uid, 'body': body})]

    def _close_calls(self, program):
        self.resumable_session_id = -1
        return [(self.client.close_session, {'token': self.uid, 'session_id': program.meta['session_id'],
                                             'body': self._session_body(program)})]

    def _finish_calls(self, program):
        return self._close_calls(program)

    def _abort_calls(self, program):
        return self._close_calls(program)

    def _snapshot_extra(self):
        return {**super()._snapshot_extra(), 'resumable_session_id': self.resumable_session_id,
                'dirty_sessions': self.dirty_sessions}


DEVICE_CLASSES = {'pico': PicoDevice, 'zymatic': ZymaticDevice, 'zseries': ZSeriesDevice}


def device_class(model):
    if model not in MODELS:
        raise CommandError(f'Unknown model {model!r}')
    return DEVICE_CLASSES[MODELS[model]['protocol']]


def new_uid(model):
    # Picos identify by a 32-char serial; Zymatic and Z-Series by a 12-char product id / token.
    return uuid.uuid4().hex if MODELS[model]['protocol'] == 'pico' else uuid.uuid4().hex[:12]


def validate_server_url(url):
    url = (url or '').strip().rstrip('/')
    if not url.startswith(('http://', 'https://')):
        raise CommandError('Server URL must start with http:// or https://')
    return url


class DeviceManager:
    def __init__(self, data_dir, default_server_url, http=None):
        self.data_dir = Path(data_dir)
        self.default_server_url = validate_server_url(default_server_url)
        self._http = http
        self._lock = threading.Lock()
        self.devices = {}
        self._load()

    @property
    def _store(self):
        return self.data_dir / 'devices.json'

    def _add(self, device):
        device.on_config_change = self.save
        with self._lock:
            self.devices[device.id] = device

    def _load(self):
        if not self._store.exists():
            return
        for cfg in json.loads(self._store.read_text()):
            self._add(device_class(cfg['model'])(
                cfg['id'], cfg['name'], cfg['model'], cfg['uid'], cfg['firmware'], cfg['server_url'],
                cfg.get('speed', 1.0), http=self._http, extra=cfg.get('extra')))

    def save(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            configs = [d.config() for d in self.devices.values()]
        self._store.write_text(json.dumps(configs, indent=2))

    def get(self, device_id):
        device = self.devices.get(device_id)
        if not device:
            raise DeviceNotFound(device_id)
        return device

    def create(self, name, model, uid=None, firmware=None, server_url=None):
        cls = device_class(model)
        uid = (uid or '').strip() or new_uid(model)
        if any(d.uid == uid for d in self.devices.values()):
            raise CommandError(f'A simulated device with UID {uid} already exists')
        device = cls(uuid.uuid4().hex[:8], (name or '').strip() or f'{MODELS[model]["label"]} {uid[:6]}',
                     model, uid, (firmware or '').strip() or MODELS[model]['firmware'],
                     validate_server_url(server_url or self.default_server_url), http=self._http)
        self._add(device)
        self.save()
        return device

    def delete(self, device_id):
        device = self.get(device_id)
        device.power_off()
        with self._lock:
            del self.devices[device_id]
        self.save()
