import json
import random
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .protocol import PicoServerClient, ProtocolError, RecipeStep

# Models share one protocol; the machine_type is what the server's /devices form expects.
MODELS = {
    'pico_s': {'label': 'Pico S', 'machine_type': 'PicoBrew', 'firmware': '0.1.34'},
    'pico_pro': {'label': 'Pico Pro', 'machine_type': 'PicoBrew', 'firmware': '0.1.34'},
}

# picoChangeState codes
STATE_READY = 2
STATE_BREWING = 3
STATE_SOUS_VIDE = 4
STATE_RINSE = 6
STATE_DEEP_CLEAN = 7

# getSession / log sesType codes
SES_BREW = 0
SES_DEEP_CLEAN = 1
SES_SOUS_VIDE = 2

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


class CommandError(Exception):
    pass


class DeviceNotFound(LookupError):
    pass


class Program:
    def __init__(self, kind, label, steps, ses_type, ses_id, state, complete_step):
        self.kind = kind
        self.label = label
        self.steps = steps
        self.ses_type = ses_type      # None = program isn't logged to the server
        self.ses_id = ses_id
        self.state = state
        self.complete_step = complete_step  # server closes the session on a step containing "complete"


class PicoDevice:
    LOG_INTERVAL_SIM_SECONDS = 10

    def __init__(self, device_id, name, model, uid, firmware, server_url, speed=1.0, http=None, tick_interval=1.0):
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
        self._http = http
        self.client = PicoServerClient(server_url, http=http)

        self.lock = threading.RLock()
        self.powered = False
        self._thread = None
        self._stop = threading.Event()

        self.registered = None
        self.firmware_update_available = None
        self.needs_cleaning = False
        self.paks = []
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
                'firmware': self.firmware, 'server_url': self.server_url, 'speed': self.speed}

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
                self.client = PicoServerClient(self.server_url, http=self._http)

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

    # ---------- power / handshake ----------
    def power_on(self):
        with self.lock:
            if not self.powered:
                self.powered = True
                self._stop = threading.Event()
                if self.tick_interval:
                    self._thread = threading.Thread(target=self._run_loop, args=(self._stop,),
                                                    name=f'pico-{self.id}', daemon=True)
                    self._thread.start()
                self._note('Powered on')
        self.handshake()

    def power_off(self):
        with self.lock:
            if self.program:
                self._note(f'Power lost during {self.program.label}', 'warn')
            self.program = None
            self.phase = None
            self.powered = False
            self.recipe = None
            self._stop.set()
            self._note('Powered off')

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

    # ---------- PicoPak ----------
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

    # ---------- programs ----------
    def start_brew(self):
        self._require_idle()
        if not self.recipe:
            raise CommandError('Insert a recognized PicoPak first')
        if not self.recipe.steps:
            raise CommandError('Recipe has no steps')
        program = Program('brew', f'Brew: {self.recipe.name}', list(self.recipe.steps), SES_BREW,
                          self.recipe.rfid, STATE_BREWING, 'Brewing Complete')
        self._start(program)

    def start_deep_clean(self):
        self._require_idle()
        ses_id = self._call(self.client.get_session, self.uid, SES_DEEP_CLEAN)
        self._start(Program('deep_clean', 'Deep Clean', list(DEEP_CLEAN_STEPS), SES_DEEP_CLEAN, ses_id,
                            STATE_DEEP_CLEAN, 'Deep Clean Complete'))
        self.needs_cleaning = False

    def start_rinse(self):
        self._require_idle()
        self._start(Program('rinse', 'Rinse', list(RINSE_STEPS), None, None, STATE_RINSE, 'Rinse Complete'))

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
        self._start(Program('sous_vide', f'Sous Vide {temperature}F / {minutes} min', steps, SES_SOUS_VIDE,
                            ses_id, STATE_SOUS_VIDE, 'Sous Vide Complete'))

    def _start(self, program):
        self._call(self.client.change_state, self.uid, program.state)
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
            label = self.program.label
            self.program = None
            self.phase = None
            self.user_paused = False
        self._note(f'Aborted {label}', 'warn')
        self._call(self.client.change_state, self.uid, STATE_READY)
        # Returning to the main menu re-queries paks, which is also what makes the server
        # close out the abandoned session file.
        self.refresh_paks()

    # ---------- faults / firmware / server admin ----------
    def report_error(self, code):
        self._require_power()
        code = int(code)
        rfid = self.recipe.rfid if self.recipe else ''
        self._call(self.client.error, self.uid, code, rfid)
        with self.lock:
            self.error_code = code
        self._note(f'Reported error {code}', 'warn')

    def clear_error(self):
        with self.lock:
            self.error_code = 0
        self._note('Error cleared')

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
                pass  # a real Pico keeps brewing when the network drops

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

        calls = []
        if program.ses_type is not None:
            calls.append((self.client.log, self._log_kwargs(program.complete_step, program.complete_step, 0)))
        calls.append((self.client.change_state, {'uid': self.uid, 'state': STATE_READY}))
        self._note(f'{program.label} complete')
        self.program = None
        self.phase = None
        return calls

    def _time_left(self):
        step = self.current_step
        if not step:
            return 0
        current = (step.step_time + step.drain_time) * 60
        if self.phase == 'holding':
            current -= self.phase_elapsed
        elif self.phase == 'draining':
            current = step.drain_time * 60 - self.phase_elapsed
        future = sum((s.step_time + s.drain_time) * 60 for s in self.program.steps[self.step_index + 1:])
        return max(0, int(current + future))

    def _log_kwargs(self, step_name, event, time_left):
        return {'uid': self.uid, 'ses_id': self.program.ses_id, 'ses_type': self.program.ses_type,
                'wort': self.wort + random.uniform(-0.3, 0.3), 'therm': self.therm + random.uniform(-0.3, 0.3),
                'step': step_name, 'time_left': time_left, 'error': self.error_code, 'event': event}

    def _log_calls(self):
        if self.program.ses_type is None:
            self._pending_event = None
            return []
        event, self._pending_event = self._pending_event, None
        self._last_log_clock = self.sim_clock
        return [(self.client.log, self._log_kwargs(self.current_step.name, event, self._time_left()))]

    def _maybe_log(self):
        due = (self._last_log_clock is None
               or self.sim_clock - self._last_log_clock >= self.LOG_INTERVAL_SIM_SECONDS)
        if self._pending_event or due:
            return self._log_calls()
        return []

    # ---------- UI ----------
    def snapshot(self, history_since=None):
        with self.lock:
            step = self.current_step
            program = None
            if self.program:
                program = {
                    'kind': self.program.kind,
                    'label': self.program.label,
                    'ses_id': self.program.ses_id,
                    'ses_type': self.program.ses_type,
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
                'powered': self.powered,
                'registered': self.registered,
                'firmware_update_available': self.firmware_update_available,
                'needs_cleaning': self.needs_cleaning,
                'paks': self.paks,
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
            }


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

    def _load(self):
        if not self._store.exists():
            return
        for cfg in json.loads(self._store.read_text()):
            device = PicoDevice(cfg['id'], cfg['name'], cfg['model'], cfg['uid'], cfg['firmware'],
                                cfg['server_url'], cfg.get('speed', 1.0), http=self._http)
            self.devices[device.id] = device

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
        if model not in MODELS:
            raise CommandError(f'Unknown model {model!r}')
        uid = (uid or '').strip() or uuid.uuid4().hex
        if any(d.uid == uid for d in self.devices.values()):
            raise CommandError(f'A simulated device with UID {uid} already exists')
        device = PicoDevice(uuid.uuid4().hex[:8], (name or '').strip() or f'{MODELS[model]["label"]} {uid[:6]}',
                            model, uid, (firmware or '').strip() or MODELS[model]['firmware'],
                            validate_server_url(server_url or self.default_server_url), http=self._http)
        with self._lock:
            self.devices[device.id] = device
        self.save()
        return device

    def delete(self, device_id):
        device = self.get(device_id)
        device.power_off()
        with self._lock:
            del self.devices[device_id]
        self.save()
