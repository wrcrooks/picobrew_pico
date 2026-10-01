import json
import shutil
from pathlib import Path

import pytest

from app import create_app
from app.main.routes_frontend import estimate_recipe_brew_chill_time

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'recipes' / 'Rincon Red.json'
GUID = 'df268ba28492421cbb21eaaaeb816f5a'


@pytest.fixture
def viewer(tmp_path):
    """Returns (get_page, write_recipe): recipes live in a temp dir seeded with Rincon Red."""
    app = create_app('tests/functional/config.test.yaml')
    recipes = tmp_path / 'recipes'
    (recipes / 'unified' / 'archive').mkdir(parents=True)
    app.config.update(RECIPES_PATH=recipes)
    target = recipes / 'unified' / 'Rincon Red.json'
    shutil.copy(EXAMPLE, target)

    def write_recipe(mutate):
        raw = json.loads(target.read_text())
        mutate(raw['VM']['Recipe'])
        target.write_text(json.dumps(raw))

    def get_page():
        with app.test_client() as client:
            response = client.get(f'/recipe?rfid={GUID}')
        assert response.status_code == 200
        return response.get_data(as_text=True)

    return get_page, write_recipe


def test_viewer_has_no_placeholders_and_real_stats(viewer):
    get_page, _ = viewer
    page = get_page()
    assert '#TODO' not in page and 'TODO' not in page
    for stat in ['1.063', '1.013', '>35<', '>15<', '6.5%']:
        assert stat in page
    assert '1.086' not in page and '9.6%' not in page


def test_viewer_derived_values(viewer):
    get_page, _ = viewer
    page = get_page()
    steps = json.loads(EXAMPLE.read_text())['VM']['Recipe']['MachineSteps']
    brew_time, chill_time = estimate_recipe_brew_chill_time({'MachineSteps': steps})
    assert f'Brew Time (Est.): {brew_time}' in page
    assert f'Chill Time (Est.): {chill_time}' in page
    assert '<b>Total Boil Time (m):</b> 60' in page
    assert '<b>Pre-hop Boil Time (m):</b> 0' in page
    assert '<b>Fermentation Type:</b> Ale' in page


def test_viewer_shows_saved_instructions(viewer):
    get_page, write_recipe = viewer
    assert 'Cool to 63°F' in get_page()

    def custom(r):
        r['FermentationInstructionsText'] = 'Let the keg cool overnight'
        r['BrewingInstructionsText'] = 'Custom brewing text'
    write_recipe(custom)
    page = get_page()
    assert 'Let the keg cool overnight' in page and 'Custom brewing text' in page


def test_viewer_other_ingredient_tables(viewer):
    get_page, write_recipe = viewer
    assert get_page().count('<p class="small text-muted text-center">None</p>') == 2

    def additions(r):
        r['Adjuncts'] = [{'Name': 'Irish Moss', 'Amount': 0.1, 'Units': 'tsp', 'Time': 15}]
        r['WhirlpoolHops'] = [{'Name': 'Citra', 'Amount': 0.5, 'Alpha': 12.0, 'Time': 20}]
        r['DryAdjuncts'] = [{'Name': 'Oak Chips', 'Amount': 1, 'Units': 'oz', 'Time': 0}]
    write_recipe(additions)
    page = get_page()
    assert 'Irish Moss' in page and 'Oak Chips' in page
    assert 'WHIRLPOOL' in page and 'Citra' in page
    assert '<b>Whirlpool Time (m):</b> 20' in page


def test_viewer_without_fermentation_steps(viewer):
    get_page, write_recipe = viewer
    write_recipe(lambda r: r.update(FermentationSteps=[]))
    assert 'Pitch Yeast' in get_page()
