import io
import json
from pathlib import Path

import pytest

from app import create_app

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'recipes' / 'Rincon Red.json'
GUID = 'df268ba28492421cbb21eaaaeb816f5a'


@pytest.fixture
def server(tmp_path):
    app = create_app('tests/functional/config.test.yaml')
    recipes = tmp_path / 'recipes'
    (recipes / 'unified' / 'archive').mkdir(parents=True)
    app.config.update(RECIPES_PATH=recipes)
    with app.test_client() as client:
        yield client, recipes / 'unified'


def upload(client, raw, filename='recipe.json'):
    body = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
    return client.post('/recipes/unified', data={'recipe': (io.BytesIO(body), filename)},
                       content_type='multipart/form-data')


def example(**changes):
    raw = json.loads(EXAMPLE.read_text())
    raw.update(changes)
    return raw


def test_upload_adds_builder_recipe(server):
    client, unified = server
    resp = upload(client, example())
    assert resp.status_code == 201 and 'Rincon Red' in resp.get_data(as_text=True)
    assert json.loads((unified / 'Rincon_Red.json').read_text())['RecipeGUID'] == GUID
    assert 'Rincon Red' in client.get('/recipes').get_data(as_text=True)


def test_upload_refuses_duplicates_without_overwriting(server):
    client, unified = server
    upload(client, example())
    before = (unified / 'Rincon_Red.json').read_text()

    assert upload(client, example()).status_code == 409                          # same RecipeGUID
    assert upload(client, example(RecipeGUID='a' * 32)).status_code == 409       # same name
    assert (unified / 'Rincon_Red.json').read_text() == before


@pytest.mark.parametrize('body', [
    b'not json',
    json.dumps({'name': 'legacy pico recipe', 'steps': []}).encode(),
    json.dumps(example(RecipeGUID='short')).encode(),
])
def test_upload_rejects_non_builder_files(server, body):
    client, unified = server
    assert upload(client, body).status_code == 400
    assert not list(unified.glob('*.json'))


def test_upload_rejects_incomplete_recipe_and_leaves_no_file(server):
    client, unified = server
    raw = example()
    del raw['VM']['Content']
    assert upload(client, raw).status_code == 400
    assert not list(unified.glob('*.json'))


def test_upload_rejects_non_json_extension(server):
    client, _ = server
    assert upload(client, example(), filename='recipe.txt').status_code == 400


def test_recipes_page_buttons_target_the_builder(server):
    client, _ = server
    page = client.get('/recipes').get_data(as_text=True)
    assert 'href="/recipe/new_from_style"' in page
    assert '/new_pico_recipe' not in page and 'upload_recipe_file_pico' not in page
