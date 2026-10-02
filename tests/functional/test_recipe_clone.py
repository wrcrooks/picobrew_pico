import json
import shutil
from pathlib import Path

import pytest

from app import create_app

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'recipes' / 'Rincon Red.json'
GUID = 'df268ba28492421cbb21eaaaeb816f5a'


@pytest.fixture
def server(tmp_path):
    app = create_app('tests/functional/config.test.yaml')
    recipes = tmp_path / 'recipes'
    for d in ['unified/archive', 'zymatic/archive', 'zseries/archive', 'pico/archive']:
        (recipes / d).mkdir(parents=True)
    app.config.update(RECIPES_PATH=recipes)
    source = recipes / 'unified' / 'Rincon Red.json'
    raw = json.loads(EXAMPLE.read_text())
    raw['TagID'] = 'clonetest00001'
    source.write_text(json.dumps(raw))
    with app.app_context():
        from app.main.routes_frontend import load_active_recipes
        load_active_recipes(None)
    with app.test_client() as client:
        yield client, recipes / 'unified', source


def clone(client):
    resp = client.post(f'/recipe/clone/{GUID}')
    assert resp.status_code == 302
    assert resp.location.startswith('/recipe/edit/')
    return resp.location.rsplit('/', 1)[1]


def test_clone_copies_recipe_with_new_identity(server):
    client, unified, source = server
    before = source.read_text()
    new_guid = clone(client)

    copy = json.loads((unified / 'Rincon_Red_(copy).json').read_text())
    original = json.loads(before)
    r, o = copy['VM']['Recipe'], original['VM']['Recipe']
    assert new_guid != GUID and copy['RecipeGUID'] == new_guid and r['GUID'] == new_guid
    assert r['PreviousGUID'] == GUID and r['RecipeID'] == 0 and r['Name'] == 'Rincon Red (copy)'
    assert copy['TagID'] is None
    for key in ('Fermentables', 'Hops', 'MachineSteps', 'Yeast', 'FermentationSteps', 'BatchSize', 'H2O'):
        assert r[key] == o[key], key
    assert source.read_text() == before

    page = client.get(f'/recipe/edit/{new_guid}')
    assert page.status_code == 200 and 'Rincon Red (copy)' in page.get_data(as_text=True)


def test_repeat_clones_get_unique_names(server):
    client, unified, _ = server
    clone(client)
    clone(client)
    assert (unified / 'Rincon_Red_(copy).json').exists() and (unified / 'Rincon_Red_(copy_2).json').exists()


def test_clone_can_be_renamed_in_editor(server):
    client, unified, _ = server
    new_guid = clone(client)
    resp = client.post(f'/recipe/edit/{new_guid}', data={'Name': 'Rincon Red Session'})
    assert resp.status_code == 302
    assert not (unified / 'Rincon_Red_(copy).json').exists()
    renamed = json.loads((unified / 'Rincon_Red_Session.json').read_text())
    assert renamed['RecipeGUID'] == new_guid and renamed['VM']['Recipe']['Name'] == 'Rincon Red Session'


def test_clone_is_served_to_machines_under_its_own_id(server):
    client, _, _ = server
    new_guid = clone(client)
    menu = client.get(f'/API/SyncUser?user={"a" * 32}&machine=zymatic00001').get_data(as_text=True)
    assert f'Rincon Red/{GUID}/' in menu and f'Rincon Red (copy)/{new_guid}/' in menu


def test_clone_rejects_get_and_unknown_recipe(server):
    client, _, _ = server
    assert client.get(f'/recipe/clone/{GUID}').status_code == 405
    assert client.post('/recipe/clone/ffffffffffffffffffffffffffffffff').status_code == 404


def test_delete_removes_only_that_recipe(server):
    client, unified, source = server
    new_guid = clone(client)
    resp = client.post(f'/recipe/delete/{new_guid}')
    assert resp.status_code == 302 and resp.location == '/recipes'
    assert not (unified / 'Rincon_Red_(copy).json').exists() and source.exists()
    menu = client.get(f'/API/SyncUser?user={"a" * 32}&machine=zymatic00001').get_data(as_text=True)
    assert new_guid not in menu and GUID in menu
    assert 'confirm-delete' in client.get('/recipes').get_data(as_text=True)


def test_delete_rejects_get_and_unknown_recipe(server):
    client, _, source = server
    assert client.get(f'/recipe/delete/{GUID}').status_code == 405
    assert client.post('/recipe/delete/ffffffffffffffffffffffffffffffff').status_code == 404
    assert source.exists()
