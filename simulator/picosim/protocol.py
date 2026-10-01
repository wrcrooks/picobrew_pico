import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlencode

import requests

# Pico location codes as the firmware numbers them (mirrors PICO_LOCATION in app/main/model.py).
PICO_LOCATION_NAMES = {
    '0': 'Prime',
    '1': 'Mash',
    '2': 'PassThru',
    '3': 'Adjunct1',
    '4': 'Adjunct2',
    '6': 'Adjunct3',
    '5': 'Adjunct4',
    '7': 'Pause',
}

# getRecipe body: NAME/ABV_TWEAK,IBU_TWEAK,ABV,IBU,[TEMP,TIME,DRAIN,LOCATION,NAME,]*|IMAGE_HEX|
_RECIPE_RE = re.compile(
    r'^(?P<name>.*?)/(?P<abv_tweak>-?[\d.]+),(?P<ibu_tweak>-?[\d.]+),(?P<abv>-?[\d.]+),(?P<ibu>-?[\d.]+),'
    r'(?P<steps>.*)\|(?P<image>[0-9a-fA-F]*)\|$',
    re.DOTALL,
)


@dataclass
class RecipeStep:
    name: str
    location: str
    temperature: int
    step_time: int
    drain_time: int

    def to_dict(self):
        return {
            'name': self.name,
            'location': self.location,
            'temperature': self.temperature,
            'step_time': self.step_time,
            'drain_time': self.drain_time,
        }


@dataclass
class PicoRecipe:
    rfid: str
    name: str
    abv: float
    ibu: float
    abv_tweak: float = -1
    ibu_tweak: float = -1
    image: str = ''
    steps: list = field(default_factory=list)

    def to_dict(self):
        return {
            'rfid': self.rfid,
            'name': self.name,
            'abv': self.abv,
            'ibu': self.ibu,
            'image': self.image,
            'steps': [s.to_dict() for s in self.steps],
        }


class ProtocolError(Exception):
    pass


def unwrap(body):
    """Strip the '#...#' framing (and surrounding CR/LFs) the Pico API wraps every payload in."""
    body = (body or '').strip()
    if len(body) >= 2 and body.startswith('#') and body.endswith('#'):
        return body[1:-1]
    return body.strip('#')


def parse_flag(body):
    return unwrap(body) == 'T'


def parse_associated_paks(body):
    paks = []
    for entry in unwrap(body).split('|'):
        if not entry.strip():
            continue
        rfid, _, name = entry.partition(',')
        paks.append({'rfid': rfid.strip(), 'name': name.strip()})
    return paks


def parse_recipe(body, rfid):
    """Returns None when the server doesn't know the tag (it answers '##')."""
    payload = unwrap(body)
    if not payload:
        return None
    match = _RECIPE_RE.match(payload)
    if not match:
        raise ProtocolError(f'Unrecognized recipe payload: {payload[:120]!r}')

    tokens = match.group('steps').split(',')
    if tokens and tokens[-1] == '':
        tokens.pop()
    if len(tokens) % 5 != 0:
        raise ProtocolError(f'Recipe step list has {len(tokens)} fields, expected a multiple of 5')

    steps = []
    for i in range(0, len(tokens), 5):
        temp, step_time, drain, location, name = tokens[i:i + 5]
        steps.append(RecipeStep(
            name=name,
            location=PICO_LOCATION_NAMES.get(location, f'Unknown({location})'),
            temperature=int(float(temp)),
            step_time=int(float(step_time)),
            drain_time=int(float(drain)),
        ))

    return PicoRecipe(
        rfid=rfid,
        name=match.group('name'),
        abv=float(match.group('abv')),
        ibu=float(match.group('ibu')),
        abv_tweak=float(match.group('abv_tweak')),
        ibu_tweak=float(match.group('ibu_tweak')),
        image=match.group('image'),
        steps=steps,
    )


class PicoServerClient:
    """Speaks the classic Pico's HTTP API to a picobrew_pico server and keeps a log of the
    traffic so the UI can show exactly what the 'device' sent and what came back."""

    def __init__(self, base_url, http=None, timeout=5.0, traffic_size=300):
        self.base_url = base_url.rstrip('/')
        self.http = http or requests.Session()
        self.timeout = timeout
        self.traffic = deque(maxlen=traffic_size)
        self._seq = 0
        self._lock = threading.Lock()

    def _record(self, entry):
        with self._lock:
            self._seq += 1
            entry['seq'] = self._seq
            self.traffic.append(entry)

    def request(self, method, path, params=None, data=None):
        query = f'?{urlencode(params)}' if params else ''
        entry = {'time': time.time(), 'method': method, 'path': f'{path}{query}'}
        started = time.monotonic()
        try:
            if method == 'GET':
                resp = self.http.get(f'{self.base_url}{path}', params=params, timeout=self.timeout)
            else:
                resp = self.http.post(f'{self.base_url}{path}', params=params, data=data, timeout=self.timeout)
        except requests.RequestException as e:
            entry.update({'status': None, 'ms': round((time.monotonic() - started) * 1000), 'error': str(e)})
            self._record(entry)
            raise ProtocolError(f'Could not reach server at {self.base_url}: {e}') from e

        entry.update({'status': resp.status_code, 'ms': round((time.monotonic() - started) * 1000),
                      'response': resp.text[:400]})
        self._record(entry)
        if resp.status_code >= 400:
            raise ProtocolError(f'{method} {path} returned HTTP {resp.status_code}')
        return resp.text

    def get(self, path, **params):
        return self.request('GET', path, params=params)

    # ---- /API/pico endpoints ----
    def register(self, uid):
        return parse_flag(self.get('/API/pico/register', uid=uid))

    def check_firmware(self, uid, version):
        return parse_flag(self.get('/API/pico/checkFirmware', uid=uid, version=version))

    def get_firmware(self, uid):
        return self.get('/API/pico/getFirmware', uid=uid)

    def get_actions_needed(self, uid):
        return unwrap(self.get('/API/pico/getActionsNeeded', uid=uid))

    def get_associated_paks(self, uid):
        return parse_associated_paks(self.get('/API/pico/getAssociatedPaks', uid=uid))

    def get_recipe(self, uid, rfid):
        return parse_recipe(self.get('/API/pico/getRecipe', uid=uid, rfid=rfid, ibu=-1, abv=-1), rfid)

    def get_session(self, uid, ses_type):
        return unwrap(self.get('/API/pico/getSession', uid=uid, sesType=ses_type))

    def change_state(self, uid, state):
        self.get('/API/pico/picoChangeState', picoUID=uid, state=state)

    def error(self, uid, code, rfid=''):
        self.get('/API/pico/error', uid=uid, code=code, rfid=rfid)

    def log(self, uid, ses_id, ses_type, wort, therm, step, time_left, error=0, event=None, shut_scale=0.0):
        params = {'uid': uid, 'sesId': ses_id, 'wort': int(round(wort)), 'therm': int(round(therm)),
                  'step': step}
        if event:
            params['event'] = event
        params.update({'error': error, 'sesType': ses_type, 'timeLeft': int(time_left),
                       'shutScale': f'{shut_scale:.2f}'})
        self.get('/API/pico/log', **params)

    # ---- server admin UI (not part of the device protocol) ----
    def register_alias(self, uid, alias, machine_type):
        """Mimics submitting the server's /devices form so the device shows up with a name."""
        return self.request('POST', '/devices', data={'machine_type': machine_type, 'uid': uid, 'alias': alias})
