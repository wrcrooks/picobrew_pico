"""Drives simulated Zymatic and Z-Series machines through the real picobrew_pico server app
(in-process), with recipes and sessions in a temp dir."""
import json
import sys
from pathlib import Path

import pytest

from picosim.devices import DeviceManager, ZSeriesDevice, ZymaticDevice
from test_against_server import FlaskTransport, REPO_ROOT

pytest.importorskip('flask_socketio')
pytest.importorskip('ruamel.yaml')

GUID = 'df268ba28492421cbb21eaaaeb816f5a'
ZSERIES_ID = str(1_000_000_000 + int(GUID[:7], 16))
CLEAN_ID = 'c' * 32


@pytest.fixture
def server(tmp_path):
    sys.path.insert(0, str(REPO_ROOT))
    from app import create_app
    from app.main import routes_frontend

    app = create_app('tests/functional/config.test.yaml')
    for d in ['recipes/unified/archive', 'recipes/zymatic/archive', 'recipes/zseries/archive',
              'recipes/pico/archive', 'sessions/brew/active', 'sessions/brew/archive']:
        (tmp_path / d).mkdir(parents=True)
    app.config.update(RECIPES_PATH=tmp_path / 'recipes', SESSIONS_PATH=tmp_path / 'sessions')

    # a builder recipe (served to both machines) and a legacy Zymatic cleaning recipe
    (tmp_path / 'recipes/unified/Rincon Red.json').write_text((REPO_ROOT / 'examples/recipes/Rincon Red.json').read_text())
    (tmp_path / 'recipes/zymatic/Cleaning_Test.json').write_text(json.dumps({
        'clean': True, 'id': CLEAN_ID, 'name': 'Cleaning Test',
        'steps': [{'name': 'Heat Water', 'temperature': 120, 'step_time': 0, 'location': 'PassThru', 'drain_time': 0},
                  {'name': 'Clean Mash', 'temperature': 120, 'step_time': 5, 'location': 'Mash', 'drain_time': 1}]}))
    with app.app_context():
        routes_frontend.load_active_recipes(None)
    yield app, tmp_path


def device(cls, app, uid, model):
    return cls('t' + uid[:5], f'Test {model}', model, uid, {'zymatic': '0.1.14', 'zseries': '0.0.116'}[model],
               'http://server', speed=60, http=FlaskTransport(app.test_client()), tick_interval=None)


def run_to_completion(d, max_ticks=600):
    for _ in range(max_ticks):
        if not d.program:
            return
        if d.phase == 'waiting':      # e.g. Rincon Red's "Connect Chiller" pause step
            d.resume()
        d.tick(10)
    raise AssertionError('program never finished')


def archived(tmp_path, pattern):
    return list((tmp_path / 'sessions/brew/archive').glob(pattern))


def test_zymatic_brews_builder_recipe(server):
    app, tmp_path = server
    d = device(ZymaticDevice, app, 'zym000000001', 'zymatic')
    d.power_on()
    assert d.profile and len(d.profile) == 32
    menu = {(m.group, m.name, m.id) for m in d.menu}
    assert ('Recipes', 'Rincon Red', GUID) in menu and ('Cleaning', 'Cleaning Test', CLEAN_ID) in menu

    d.select_recipe(GUID)
    assert [s.name for s in d.recipe.steps][:2] == ['Heat Water', 'Mash']
    d.start_brew()
    session = d.program.meta['session']
    run_to_completion(d)
    assert d.last_comm_error is None

    files = archived(tmp_path, f'*#zym000000001#{session}#Rincon_Red.json')
    assert len(files) == 1
    points = json.loads(files[0].read_text())
    assert {'wort', 'heat1', 'board', 'heat2', 'recovery', 'state'} <= set(points[0])
    assert [p['event'] for p in points if 'event' in p] == [s.name for s in d.recipe.steps]
    assert max(p['wort'] for p in points) >= 200


def test_zymatic_runs_cleaning_recipe_from_clean_menu(server):
    app, tmp_path = server
    d = device(ZymaticDevice, app, 'zym000000002', 'zymatic')
    d.power_on()
    d.select_recipe(CLEAN_ID)
    d.start_brew()
    assert d.program.label == 'Clean: Cleaning Test'
    run_to_completion(d)
    assert len(archived(tmp_path, '*#zym000000002#*#Cleaning_Test.json')) == 1


def test_zseries_brews_builder_recipe(server):
    app, tmp_path = server
    d = device(ZSeriesDevice, app, 'zser00000001', 'zseries')
    d.power_on()
    assert d.registered and d.resumable_session_id == -1
    assert any(m.id == ZSERIES_ID and m.name == 'Rincon Red' for m in d.menu)

    d.select_recipe(ZSERIES_ID)
    assert d.recipe.start_water == 13.9 and len(d.recipe.steps) == 8
    d.start_brew()
    run_to_completion(d)
    assert d.last_comm_error is None

    files = archived(tmp_path, '*#zser00000001#*#Rincon_Red#6.json')
    assert len(files) == 1
    points = json.loads(files[0].read_text())
    assert max(p['wort'] for p in points) > 200        # Celsius logs converted back to °F by the server
    assert [p['event'] for p in points if 'event' in p][:2] == ['Heat Water', 'Mash']


def test_zseries_resumes_session_after_power_loss(server):
    app, tmp_path = server
    d = device(ZSeriesDevice, app, 'zser00000002', 'zseries')
    d.power_on()
    d.select_recipe(ZSERIES_ID)
    d.start_brew()
    session_id = d.program.meta['session_id']
    while d.current_step.name != 'Mash' or d.phase != 'holding':
        d.tick(10)
    for _ in range(3):
        d.tick(10)

    d.power_off()
    d.power_on()
    assert d.resumable_session_id == session_id

    d.resume_session()
    assert d.program.label == 'Resumed: Rincon Red'
    assert [s.name for s in d.program.steps][0] == 'Mash'
    assert 0 < d.program.steps[0].step_time < 90       # only the remaining mash time
    run_to_completion(d)
    assert len(archived(tmp_path, '*#zser00000002#*#Rincon_Red#6.json')) == 1


def test_zseries_rinse_and_clean_programs(server):
    app, tmp_path = server
    d = device(ZSeriesDevice, app, 'zser00000003', 'zseries')
    d.power_on()
    for key, name, session_type in [('rinse', 'Rinse', 0), ('clean', 'Clean', 1)]:
        d.start_program(key)
        run_to_completion(d)
        assert len(archived(tmp_path, f'*#zser00000003#*#{name}#{session_type}.json')) == 1


def test_manager_creates_each_model(tmp_path):
    m = DeviceManager(tmp_path, 'http://server')
    zym, zser = m.create('Z', 'zymatic'), m.create('Z2', 'zseries')
    assert isinstance(zym, ZymaticDevice) and len(zym.uid) == 12 and zym.firmware == '0.1.14'
    assert isinstance(zser, ZSeriesDevice) and len(zser.uid) == 12
    zym._save_extra(profile_guid='p' * 32)
    reloaded = DeviceManager(tmp_path, 'http://server')
    assert reloaded.get(zym.id).profile == 'p' * 32 and isinstance(reloaded.get(zser.id), ZSeriesDevice)
