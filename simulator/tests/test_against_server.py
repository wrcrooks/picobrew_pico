"""Drives a simulated Pico through the real picobrew_pico server app (in-process, via Flask's
test client), with recipes and sessions redirected to a temp dir."""
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

from conftest import FakeResponse
from picosim.devices import PicoDevice

REPO_ROOT = Path(__file__).resolve().parents[2]
RFID = 'simtest0000001'

pytest.importorskip('flask_socketio')
pytest.importorskip('ruamel.yaml')


class FlaskTransport:
    def __init__(self, client):
        self.client = client

    def get(self, url, params=None, timeout=None):
        resp = self.client.get(urlparse(url).path, query_string=params)
        return FakeResponse(resp.get_data(as_text=True), resp.status_code)

    def post(self, url, params=None, data=None, json=None, timeout=None):
        resp = self.client.post(urlparse(url).path, query_string=params, data=data, json=json)
        return FakeResponse(resp.get_data(as_text=True), resp.status_code)

    def put(self, url, params=None, json=None, timeout=None):
        resp = self.client.put(urlparse(url).path, query_string=params, json=json)
        return FakeResponse(resp.get_data(as_text=True), resp.status_code)


@pytest.fixture
def server(tmp_path):
    sys.path.insert(0, str(REPO_ROOT))
    from app import create_app
    from app.main import routes_frontend

    app = create_app('tests/functional/config.test.yaml')
    app.config.update(RECIPES_PATH=tmp_path / 'recipes', SESSIONS_PATH=tmp_path / 'sessions')
    for d in ['recipes/pico/archive', 'recipes/unified/archive', 'sessions/brew/active', 'sessions/brew/archive']:
        (tmp_path / d).mkdir(parents=True)

    recipe = {
        'id': RFID, 'name': 'Sim Test IPA', 'abv': 6.5, 'ibu': 55, 'image': '',
        'steps': [
            {'name': 'Preparing To Brew', 'location': 'Prime', 'temperature': 0, 'step_time': 3, 'drain_time': 0},
            {'name': 'Heating', 'location': 'PassThru', 'temperature': 110, 'step_time': 0, 'drain_time': 0},
            {'name': 'Mash 1', 'location': 'Mash', 'temperature': 152, 'step_time': 30, 'drain_time': 0},
            {'name': 'Mash Out', 'location': 'Mash', 'temperature': 178, 'step_time': 5, 'drain_time': 2},
            {'name': 'Hops 1', 'location': 'Adjunct1', 'temperature': 202, 'step_time': 10, 'drain_time': 0},
            {'name': 'Hops 2', 'location': 'Adjunct2', 'temperature': 202, 'step_time': 5, 'drain_time': 5},
        ],
    }
    (tmp_path / 'recipes/pico/Sim_Test_IPA.json').write_text(json.dumps(recipe))
    with app.app_context():
        routes_frontend.load_active_recipes(None)
    yield app, tmp_path


def test_brew_session_recorded_by_server(server):
    app, data = server
    device = PicoDevice('it', 'Integration Pico', 'pico_s', 'simtest' + '0' * 25, '0.1.34', 'http://server',
                        speed=60, http=FlaskTransport(app.test_client()), tick_interval=None)

    device.power_on()
    assert device.registered
    assert {'rfid': RFID, 'name': 'Sim Test IPA'} in device.paks

    recipe = device.insert_pak(RFID)
    assert recipe.name == 'Sim Test IPA' and len(recipe.steps) == 6

    device.start_brew()
    for _ in range(500):
        if not device.program:
            break
        device.tick(10)
    assert device.program is None and device.last_comm_error is None

    archived = list((data / 'sessions/brew/archive').glob(f'*#{device.uid}#{RFID}#*.json'))
    assert len(archived) == 1 and 'Sim_Test_IPA' in archived[0].name
    points = json.loads(archived[0].read_text())
    assert [p['event'] for p in points if 'event' in p] == [
        'Preparing To Brew', 'Heating', 'Mash 1', 'Mash Out', 'Hops 1', 'Hops 2', 'Brewing Complete']
    assert points[-1]['timeLeft'] == 0
    assert not list((data / 'sessions/brew/active').iterdir())
