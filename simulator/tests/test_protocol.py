import pytest

from conftest import BREW_STEPS, wire_recipe
from picosim.protocol import ProtocolError, parse_associated_paks, parse_flag, parse_recipe, unwrap


def test_unwrap_and_flags():
    assert unwrap('#T#\r\n') == 'T'
    assert unwrap('\r\n#abc#\r\n\r\n') == 'abc'
    assert parse_flag('#T#')
    assert not parse_flag('#F#')


def test_parse_associated_paks():
    body = '\r\n#aabbccddeeff00,Test IPA|11223344556677,Stout, Extra|#\r\n\r\n'
    assert parse_associated_paks(body) == [
        {'rfid': 'aabbccddeeff00', 'name': 'Test IPA'},
        {'rfid': '11223344556677', 'name': 'Stout, Extra'},
    ]
    assert parse_associated_paks('\r\n##\r\n\r\n') == []


def test_parse_recipe_matches_server_serialization():
    image = 'ff' * 1024
    recipe = parse_recipe(wire_recipe('Pale Ale/Session', BREW_STEPS, abv=5.2, ibu=33.0, image=image), 'tag1')
    assert recipe.name == 'Pale Ale/Session'
    assert (recipe.abv, recipe.ibu) == (5.2, 33.0)
    assert recipe.image == image
    assert [s.name for s in recipe.steps] == [s[0] for s in BREW_STEPS]
    hops4 = recipe.steps[-1]
    assert (hops4.location, hops4.temperature, hops4.step_time, hops4.drain_time) == ('Adjunct4', 202, 8, 5)
    assert recipe.steps[0].location == 'Prime'


def test_unknown_recipe_is_none():
    assert parse_recipe('##', 'nope') is None


def test_garbage_recipe_raises():
    with pytest.raises(ProtocolError):
        parse_recipe('#not a recipe#', 'x')
