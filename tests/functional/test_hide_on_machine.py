import json
import re
import shutil
from pathlib import Path

import pytest

from app import create_app

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'recipes' / 'Rincon Red.json'
GUID = 'df268ba28492421cbb21eaaaeb816f5a'
ZSERIES_ID = 1_000_000_000 + int(GUID[:7], 16)
TAG = 'hidetest000001'
ZERO_USER = '0' * 32


@pytest.fixture
def server(tmp_path):
    app = create_app('tests/functional/config.test.yaml')
    recipes = tmp_path / 'recipes'
    for d in ['unified/archive', 'zymatic/archive', 'zseries/archive', 'pico/archive']:
        (recipes / d).mkdir(parents=True)
    for d in ['brew/active', 'brew/archive']:
        (tmp_path / 'sessions' / d).mkdir(parents=True)
    app.config.update(RECIPES_PATH=recipes, SESSIONS_PATH=tmp_path / 'sessions')
    target = recipes / 'unified' / 'Rincon Red.json'
    shutil.copy(EXAMPLE, target)
    raw = json.loads(target.read_text())
    raw['TagID'] = TAG
    target.write_text(json.dumps(raw))
    with app.app_context():
        from app.main.routes_frontend import load_active_recipes
        load_active_recipes(None)
    with app.test_client() as client:
        yield app, client, target


def zymatic_menu(client, user='a' * 32):
    return client.get(f'/API/SyncUser?user={user}&machine=zymatic00001').get_data(as_text=True)


def zseries_menu(client):
    resp = client.post('/Vendors/input.cshtml?ctl=RecipeRefListController&token=ztoken',
                       json={'Kind': 0, 'MaxCount': 50, 'Offset': 0})
    return resp.get_json()['Recipes']


def pico_paks(client):
    return client.get('/API/pico/getAssociatedPaks?uid=pico').get_data(as_text=True)


def set_hidden(client, hidden):
    return client.post(f'/recipe/{GUID}/hide_on_machine', json={'hidden': hidden})


def test_builder_recipe_served_in_each_machine_format(server):
    _, client, _ = server
    menu = zymatic_menu(client)
    assert (f'Rincon Red/{GUID}/Heat Water,152,0,0,0/Mash,152,90,1,8/Heat to Boil,207,0,0,0/'
            'Boil Adjunct 1,207,40,2,0/Boil Adjunct 2,207,15,3,0/Boil Adjunct 3,207,5,4,5/'
            'Connect Chiller,0,0,6,0/Chill,63,10,0,10/|') in menu
    assert 'Rincon Red' not in zymatic_menu(client, ZERO_USER)  # cleaning menu stays cleaning-only

    assert {'ID': ZSERIES_ID, 'Name': 'Rincon Red', 'Kind': 0, 'Uri': None, 'Abv': -1, 'Ibu': -1} in zseries_menu(client)
    detail = client.get(f'/Vendors/input.cshtml?type=Recipe&token=ztoken&id={ZSERIES_ID}').get_json()
    assert detail['ID'] == ZSERIES_ID and detail['StartWater'] == 13.9  # 3.66 gal in liters
    assert detail['Steps'][1] == {'Name': 'Mash', 'Location': 1, 'Temp': 152, 'Time': 90, 'Drain': 8}
    assert detail['Steps'][6]['Location'] == 6  # Pause

    assert f'{TAG},Rincon Red|' in pico_paks(client)


def test_hide_on_machine_toggle(server):
    app, client, target = server
    assert set_hidden(client, True).get_json() == {'id': GUID, 'hidden': True}
    assert json.loads(target.read_text())['HideOnMachine'] is True

    assert 'Rincon Red' not in zymatic_menu(client)
    assert all(r['Name'] != 'Rincon Red' for r in zseries_menu(client))
    assert TAG not in pico_paks(client)

    # sessions started before hiding must still resolve the recipe
    with app.test_request_context():
        from app.main import routes_zymatic_api, routes_zseries_api
        assert routes_zymatic_api.get_recipe_name_by_id(GUID) == 'Rincon Red'
        assert routes_zseries_api.get_recipe_by_name('Rincon Red').id == ZSERIES_ID
    assert client.get(f'/Vendors/input.cshtml?type=Recipe&token=ztoken&id={ZSERIES_ID}').status_code == 200

    set_hidden(client, False)
    assert 'Rincon Red' in zymatic_menu(client)
    assert any(r['Name'] == 'Rincon Red' for r in zseries_menu(client))
    assert TAG in pico_paks(client)


def test_recipes_page_shows_toggle_and_status(server):
    _, client, _ = server
    page = client.get('/recipes').get_data(as_text=True)
    assert f'id="hide-on-machine-{GUID}"' in page and 'Hide on Machine' in page
    set_hidden(client, True)
    page = client.get('/recipes').get_data(as_text=True)
    assert re.search(rf'id="hide-on-machine-{GUID}"[^>]*\bchecked\b', page)


@pytest.mark.parametrize('mutate, reason', [
    (lambda raw: raw.update(UseMetric=True), 'metric recipes are not supported'),
    (lambda raw: raw['VM']['Recipe']['MachineSteps'][0].update(StepLocation=9), "unknown location 9"),
    (lambda raw: raw['VM']['Recipe']['MachineSteps'][0].update(Temperature=300), 'outside 0-212'),
    (lambda raw: raw['VM']['Recipe'].update(MachineSteps=[]), 'no machine steps'),
])
def test_unsafe_recipes_are_not_served(server, mutate, reason):
    _, client, target = server
    raw = json.loads(target.read_text())
    mutate(raw)
    target.write_text(json.dumps(raw))
    assert 'Rincon Red' not in zymatic_menu(client)
    assert all(r['Name'] != 'Rincon Red' for r in zseries_menu(client))
    assert reason in client.get('/recipes').get_data(as_text=True)


def test_zymatic_names_are_cleaned_of_delimiters(server):
    _, client, target = server
    raw = json.loads(target.read_text())
    raw['VM']['Recipe']['Name'] = 'Red/Amber|#1, v2'
    raw['VM']['Recipe']['MachineSteps'][0]['Name'] = 'Heat, then/hold'
    target.write_text(json.dumps(raw))
    menu = zymatic_menu(client)
    assert f'Red-Amber--1- v2/{GUID}/Heat- then-hold,152,0,0,0/' in menu


def test_toggle_validation(server):
    _, client, _ = server
    assert client.post(f'/recipe/{GUID}/hide_on_machine', json={'hidden': 'yes'}).status_code == 400
    assert client.post(f'/recipe/{GUID}/hide_on_machine', data='nope').status_code == 400
    assert client.post('/recipe/ffffffffffffffffffffffffffffffff/hide_on_machine', json={'hidden': True}).status_code == 404


def test_zseries_session_with_builder_recipe_can_resume(server):
    """Start, log and resume a Z-Series brew of a builder recipe while another (idle) machine
    is known to the server, then close it."""
    _, client, _ = server
    client.get('/API/pico/register?uid=idle-pico-for-resume-test')
    token = 'zresume0001'
    session = {'Name': 'Rincon Red', 'SessionType': 6, 'DurationSec': 0, 'FirmwareVersion': '0.0.116',
               'GroupSession': False, 'MaxTemp': 0, 'MaxTempAddedSec': 0, 'PressurePa': 0,
               'ZProgramId': 1, 'RecipeID': ZSERIES_ID}
    created = client.post(f'/Vendors/input.cshtml?type=ZSession&token={token}', json=session).get_json()
    assert created['Name'] == 'Rincon Red'

    log = {'ZSessionID': created['ID'], 'StepName': 'Mash', 'SecondsRemaining': 1200, 'TargetTemp': 66.7,
           'AmbientTemp': 20, 'DrainTemp': 60, 'WortTemp': 66, 'ThermoBlockTemp': 70, 'ValvePosition': 1}
    assert client.post(f'/Vendors/input.cshtml?type=ZSessionLog&token={token}', json=log).status_code == 200

    resumed = client.get(f'/Vendors/input.cshtml?type=ResumableSession&token={token}&id={created["ID"]}')
    assert resumed.status_code == 200, resumed.get_data(as_text=True)
    steps = resumed.get_json()['Recipe']['Steps']
    assert [s['Name'] for s in steps][:2] == ['Mash', 'Heat to Boil'] and steps[0]['Time'] == 1200

    closed = client.post(f'/Vendors/input.cshtml?type=ZSession&token={token}&id={created["ID"]}', json=session)
    assert closed.status_code == 200
