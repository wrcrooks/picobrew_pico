import json
from pathlib import Path

import pytest

from app import create_app

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'recipes' / 'Rincon Red.json'
TAG = 'historytag0001'
PICO_UID = 'a' * 32


def write_recipe(unified, filename, name, guid, tag=None):
    raw = json.loads(EXAMPLE.read_text())
    raw['RecipeGUID'] = guid
    raw['TagID'] = tag
    raw['VM']['Recipe']['Name'] = name
    (unified / filename).write_text(json.dumps(raw))


@pytest.fixture
def server(tmp_path):
    app = create_app('tests/functional/config.test.yaml')
    unified = tmp_path / 'recipes' / 'unified'
    (unified / 'archive').mkdir(parents=True)
    archive = tmp_path / 'sessions' / 'brew' / 'archive'
    for d in ['brew/active', 'brew/archive']:
        (tmp_path / 'sessions' / d).mkdir(parents=True)
    app.config.update(RECIPES_PATH=tmp_path / 'recipes', SESSIONS_PATH=tmp_path / 'sessions')

    write_recipe(unified, 'Rincon_Red.json', 'Rincon Red', '1' * 32, tag=TAG)
    write_recipe(unified, 'Red_Amber.json', 'Red/Amber #2', '2' * 32)
    write_recipe(unified, 'Snake_Case.json', 'snake_case ale', '3' * 32)
    write_recipe(unified, 'Never_Brewed.json', 'Never Brewed', '4' * 32)

    for name in [
        f'20260105_090000#{PICO_UID}#{TAG}#Renamed_Before.json',        # Pico: tag match only
        f'20260210_100000#{PICO_UID}#{TAG}#Rincon_Red.json',            # Pico: tag + name, counted once
        '20260301_120000#zseries0001#abcdef#Rincon_Red#6.json',         # Z-Series: name match
        '20260302_120000#zseries0001#abcdef#Deep_Clean#1.json',         # unrelated
        '20260115_080000#zymatic00001#sess01#Red-Amber_-2.json',        # Zymatic-safe name of 'Red/Amber #2'
        '20260116_080000#zymatic00001#sess02#Red%2FAmber_%232.json',    # not a name the server writes
        '20260120_080000#zseries0001#abcdef#snake_case_ale#6.json',     # underscores in the real name
        'not-a-session.json',
        'baddate#uid#x#Rincon_Red.json',
    ]:
        (archive / name).write_text('[]')

    with app.test_client() as client:
        yield client


def card(page, name):
    start = page.index(f'<h3 class="mb-0">{name}</h3>')
    return page[start:page.index('</table>', start)]


def test_brew_history_on_recipes_page(server):
    page = server.get('/recipes').get_data(as_text=True)
    assert 'Mon Feb 05 2018' not in page

    rincon = card(page, 'Rincon Red')
    assert 'Brewed: <b>3 times</b>' in rincon and 'Sun Mar 01 2026' in rincon

    amber = card(page, 'Red/Amber #2')
    assert 'Brewed: <b>1 time</b>' in amber and 'Thu Jan 15 2026' in amber

    snake = card(page, 'snake_case ale')
    assert 'Brewed: <b>1 time</b>' in snake and 'Tue Jan 20 2026' in snake

    assert 'Not brewed yet' in card(page, 'Never Brewed')
