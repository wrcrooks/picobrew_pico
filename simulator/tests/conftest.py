import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def wire_recipe(name, steps, abv=6.0, ibu=40.0, image=''):
    """Builds a getRecipe body the same way the server's PicoBrewRecipe.serialize() does."""
    body = ''.join(f'{t},{time},{drain},{loc},{step_name},' for step_name, loc, t, time, drain in steps)
    return f'#{name}/-1,-1,{abv},{ibu},{body}|{image}|#'


BREW_STEPS = [
    ('Preparing To Brew', '0', 0, 3, 0),
    ('Heating', '2', 110, 0, 0),
    ('Dough In', '1', 110, 7, 0),
    ('Mash 1', '1', 148, 45, 0),
    ('Mash Out', '1', 178, 7, 2),
    ('Hops 1', '3', 202, 10, 0),
    ('Hops 4', '5', 202, 8, 5),
]


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakePicoServer:
    """In-memory stand-in for the server's /API/pico endpoints, recording every call."""

    def __init__(self):
        self.calls = []
        self.recipes = {'aabbccddeeff00': ('Test IPA', BREW_STEPS)}
        self.firmware_update = False
        self.actions_needed = ''
        self.offline = False

    def paths(self, path):
        return [params for p, params in self.calls if p == path]

    def get(self, url, params=None, timeout=None):
        import requests
        if self.offline:
            raise requests.ConnectionError('connection refused')
        path = urlparse(url).path
        params = {k: str(v) for k, v in (params or {}).items()}
        self.calls.append((path, params))
        if path == '/API/pico/register':
            return FakeResponse('#T#\r\n')
        if path == '/API/pico/checkFirmware':
            return FakeResponse('#T#' if self.firmware_update else '#F#')
        if path == '/API/pico/getActionsNeeded':
            return FakeResponse(f'#{self.actions_needed}#')
        if path == '/API/pico/getAssociatedPaks':
            paks = ''.join(f'{rfid},{name}|' for rfid, (name, _) in self.recipes.items())
            return FakeResponse(f'\r\n#{paks}#\r\n\r\n')
        if path == '/API/pico/getRecipe':
            recipe = self.recipes.get(params['rfid'])
            return FakeResponse(wire_recipe(recipe[0], recipe[1]) if recipe else '##')
        if path == '/API/pico/getSession':
            return FakeResponse('#0123456789abcdef0123#\r\n')
        if path in ('/API/pico/log', '/API/pico/picoChangeState', '/API/pico/error'):
            return FakeResponse('\r\n')
        if path == '/API/pico/getFirmware':
            return FakeResponse('#F#')
        return FakeResponse('not found', 404)

    def post(self, url, params=None, data=None, timeout=None):
        self.calls.append((urlparse(url).path, dict(data or {})))
        return FakeResponse('<html></html>')


@pytest.fixture
def fake_server():
    return FakePicoServer()
