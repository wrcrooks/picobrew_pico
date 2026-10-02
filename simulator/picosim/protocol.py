import json
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

# Zymatic and Z-Series share one numbering (ZYMATIC_LOCATION / ZSERIES_LOCATION in app/main/model.py).
MACHINE_LOCATION_NAMES = {
    '0': 'PassThru',
    '1': 'Mash',
    '2': 'Adjunct1',
    '3': 'Adjunct2',
    '4': 'Adjunct3',
    '5': 'Adjunct4',
    '6': 'Pause',
}
MACHINE_LOCATION_CODES = {name: int(code) for code, name in MACHINE_LOCATION_NAMES.items()}

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

    @property
    def id(self):
        return self.rfid

    def to_dict(self):
        return {
            'rfid': self.rfid,
            'name': self.name,
            'abv': self.abv,
            'ibu': self.ibu,
            'image': self.image,
            'steps': [s.to_dict() for s in self.steps],
        }


@dataclass
class MenuRecipe:
    """A recipe picked from a Zymatic/Z-Series menu. Z-Series menu entries carry no steps
    (steps=None) until the recipe's details are fetched."""
    id: str
    name: str
    group: str
    steps: list = None
    start_water: float = None

    def menu_entry(self):
        return {'id': self.id, 'name': self.name, 'group': self.group}

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'group': self.group,
            'start_water': self.start_water,
            'steps': [s.to_dict() for s in self.steps or []],
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


class ServerClient:
    """HTTP client for one simulated device that keeps a log of the traffic so the UI can
    show exactly what the 'device' sent and what came back."""

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

    def request(self, method, path, params=None, data=None, json_body=None):
        query = f'?{urlencode(params)}' if params else ''
        entry = {'time': time.time(), 'method': method, 'path': f'{path}{query}'}
        if json_body is not None:
            entry['body'] = json.dumps(json_body)[:400]
        url = f'{self.base_url}{path}'
        started = time.monotonic()
        try:
            if method == 'GET':
                resp = self.http.get(url, params=params, timeout=self.timeout)
            elif json_body is not None:
                send = self.http.put if method == 'PUT' else self.http.post
                resp = send(url, params=params, json=json_body, timeout=self.timeout)
            else:
                resp = self.http.post(url, params=params, data=data, timeout=self.timeout)
        except requests.RequestException as e:
            entry.update({'status': None, 'ms': round((time.monotonic() - started) * 1000), 'error': str(e)})
            self._record(entry)
            raise ProtocolError(f'Could not reach server at {self.base_url}: {e}') from e

        entry.update({'status': resp.status_code, 'ms': round((time.monotonic() - started) * 1000),
                      'response': resp.text[:400]})
        self._record(entry)
        if resp.status_code >= 400:
            raise ProtocolError(f'{method} {path} returned HTTP {resp.status_code}: {resp.text[:200]}')
        return resp.text

    def get(self, path, **params):
        return self.request('GET', path, params=params)

    # ---- server admin UI (not part of any device protocol) ----
    def register_alias(self, uid, alias, machine_type):
        """Mimics submitting the server's /devices form so the device shows up with a name."""
        return self.request('POST', '/devices', data={'machine_type': machine_type, 'uid': uid, 'alias': alias})


class PicoServerClient(ServerClient):
    """The classic Pico's /API/pico endpoints."""

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


# ---------------- Zymatic ----------------
ZYMATIC_CLEAN_USER = '0' * 32   # SyncUser with this profile returns the cleaning menu


def parse_zymatic_menu(body, group):
    """SyncUser body: NAME/RECIPE_GUID/[STEP,TEMP,TIME,LOCATION,DRAIN/]* then '|', repeated."""
    recipes = []
    for entry in unwrap(body).split('|'):
        parts = [p for p in entry.split('/') if p]
        if len(parts) < 2:
            continue
        name, recipe_id, steps = parts[0], parts[1], []
        for raw in parts[2:]:
            fields = raw.split(',')
            if len(fields) != 5:
                raise ProtocolError(f'Unrecognized Zymatic step {raw!r} in recipe {name!r}')
            step_name, temp, step_time, location, drain = fields
            steps.append(RecipeStep(step_name, MACHINE_LOCATION_NAMES.get(location, f'Unknown({location})'),
                                    int(float(temp)), int(float(step_time)), int(float(drain))))
        recipes.append(MenuRecipe(id=recipe_id, name=name, group=group, steps=steps))
    return recipes


class ZymaticClient(ServerClient):
    def user_setup(self, machine):
        """'#PROFILE_GUID/USER_NAME|#' - the profile the machine syncs recipes for."""
        return unwrap(self.get('/API/usersetup', machine=machine, admin=0)).rstrip('|').split('/')[0]

    def firmware_check(self, machine, firmware):
        ver, maj, minor = (list(map(int, firmware.split('.'))) + [0, 0, 0])[:3]
        return parse_flag(self.get('/API/zymaticFirmwareCheck', machine=machine, ver=ver, maj=maj, min=minor))

    def sync_user(self, user, machine, group):
        return parse_zymatic_menu(self.get('/API/SyncUser', user=user, machine=machine), group)

    def check_sync(self, user):
        return unwrap(self.get('/API/checksync', user=user)) == '+'

    def start_session(self, user, recipe_id, machine, firmware):
        return unwrap(self.get('/API/logSession', user=user, recipe=recipe_id, code=0, machine=machine, firm=firmware))

    def log_event(self, session, step_name, state):
        self.get('/API/logsession', session=session, code=1, data=step_name, state=state)

    def log_temperatures(self, session, wort, heat1, board, heat2, recovery, state):
        # the server parses each value as int(token[2:]): a 2-character label, then the °F value
        data = '|'.join(f'{label}{int(round(v))}' for label, v in
                        (('WT', wort), ('H1', heat1), ('BD', board), ('H2', heat2)))
        self.get('/API/LogSession', session=session, code=2, data=data, step=recovery, state=state)

    def end_session(self, session):
        self.get('/API/logsession', session=session, code=3)

    def session_error(self, machine, session, code):
        self.get('/API/sessionerror', machine=machine, session=session, errorcode=code)


# ---------------- Z-Series ----------------
class ZSeriesClient(ServerClient):
    ENDPOINT = '/Vendors/input.cshtml'

    def _json(self, method, token, body, **params):
        return json.loads(self.request(method, self.ENDPOINT, params={**params, 'token': token}, json_body=body))

    def zstate(self, token, firmware, boiler_type):
        return self._json('PUT', token, {'BoilerType': boiler_type, 'CurrentFirmware': firmware}, type='ZState')

    def recipe_list(self, token):
        body = self._json('POST', token, {'Kind': 0, 'MaxCount': 50, 'Offset': 0}, ctl='RecipeRefListController')
        return [MenuRecipe(id=str(r['ID']), name=r['Name'], group='Recipes', steps=None) for r in body['Recipes']]

    def recipe(self, token, recipe_id):
        body = json.loads(self.get(self.ENDPOINT, type='Recipe', token=token, id=recipe_id))
        return MenuRecipe(id=str(body['ID']), name=body['Name'], group='Recipes',
                          steps=[zseries_step(s) for s in body['Steps']], start_water=body.get('StartWater'))

    def create_session(self, token, body):
        return self._json('POST', token, body, type='ZSession')

    def log(self, token, body):
        return self._json('POST', token, body, type='ZSessionLog')

    def close_session(self, token, session_id, body):
        return self._json('PUT', token, body, type='ZSession', id=session_id)

    def resumable_session(self, token, session_id):
        return json.loads(self.get(self.ENDPOINT, type='ResumableSession', token=token, id=session_id))


def zseries_step(s):
    return RecipeStep(s['Name'], MACHINE_LOCATION_NAMES.get(str(s['Location']), f'Unknown({s["Location"]})'),
                      int(s['Temp']), int(s['Time']), int(s['Drain']))
