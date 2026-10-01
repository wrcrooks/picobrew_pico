import copy
import json
from pathlib import Path

import pytest

from app.main.recipe_conditions import condition_charts

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'recipes' / 'Rincon Red.json'


@pytest.fixture
def recipe():
    """The viewer's recipe dict shape (ReduxRecipe attributes) for the example recipe."""
    raw = json.loads(EXAMPLE.read_text())['VM']['Recipe']
    return dict(raw, ibu=raw['IBU'], abv=raw['ABV'])


def by_key(data):
    return {c['key']: c for c in data['conditions']}


def at(cond, x):
    i = min(range(len(cond['x'])), key=lambda k: abs(cond['x'][k] - x))
    return {s: cond['series'][s]['value'][i] for s in cond['series']}


def test_nominal_matches_recipe_stats(recipe):
    data = condition_charts(recipe)
    assert data['nominal']['OG'] == pytest.approx(1.063, abs=5e-4)
    assert data['nominal']['FG'] == pytest.approx(1.013, abs=5e-4)
    assert data['nominal']['ABV'] == pytest.approx(6.5, abs=1e-6)
    assert data['nominal']['IBU'] == pytest.approx(35, abs=0.5)
    assert data['nominal']['SRM'] == pytest.approx(15, abs=0.5)
    for cond in data['conditions']:
        point = at(cond, cond['nominal'])
        for stat, value in data['nominal'].items():
            assert point[stat] == pytest.approx(value, abs=1e-3), (cond['key'], stat)


def test_condition_trends(recipe):
    c = by_key(condition_charts(recipe))
    eff, vol, att = c['efficiency'], c['volume'], c['attenuation']

    og = eff['series']['OG']['value']
    assert og == sorted(og) and og[0] < og[-1]
    assert len(set(eff['series']['SRM']['value'])) == 1

    for stat in ('OG', 'IBU', 'SRM'):
        values = vol['series'][stat]['value']
        assert values == sorted(values, reverse=True) and values[0] > values[-1], stat

    fg, abv = att['series']['FG']['value'], att['series']['ABV']['value']
    assert fg == sorted(fg, reverse=True) and abv == sorted(abv)
    assert len(set(att['series']['OG']['value'])) == 1


def test_attenuation_band_only_for_fg_abv(recipe):
    c = by_key(condition_charts(recipe))
    for key in ('efficiency', 'volume'):
        series = c[key]['series']
        assert series['OG']['low'] is None and series['IBU']['low'] is None
        for stat in ('FG', 'ABV'):
            lo, mid, hi = series[stat]['low'], series[stat]['value'], series[stat]['high']
            assert all(l <= m <= h for l, m, h in zip(lo, mid, hi)), (key, stat)
    assert c['attenuation']['series']['FG']['low'] is None


def test_style_range_and_yeast_range(recipe):
    data = condition_charts(recipe)
    assert data['style']['OG'] == [1.044, 1.06]
    assert data['style']['IBU'] == [17.0, 28.0]
    assert data['attenuation_range'] == [75.0, 85.0]


def test_missing_inputs_degrade(recipe):
    bare = copy.deepcopy(recipe)
    bare.update(Efficiency=None, Yeast=None, BeerStyle=None)
    data = condition_charts(bare)
    assert [c['key'] for c in data['conditions']] == ['volume', 'attenuation']
    assert data['style']['OG'] == [None, None]
    assert data['attenuation_range'] == [70.0, 80.0]
