import pytest

from picosim.devices import CommandError, DeviceManager, PicoDevice

RFID = 'aabbccddeeff00'


def make_device(fake_server, speed=60):
    return PicoDevice('dev1', 'Sim', 'pico_s', 'u' * 32, '0.1.34', 'http://server', speed=speed,
                      http=fake_server, tick_interval=None)


def run_to_completion(device, max_ticks=500):
    for _ in range(max_ticks):
        if not device.program:
            return
        device.tick(10)
    raise AssertionError('program never finished')


def test_handshake_sequence(fake_server):
    fake_server.firmware_update = True
    fake_server.actions_needed = '7'
    d = make_device(fake_server)
    d.power_on()
    assert [p for p, _ in fake_server.calls] == [
        '/API/pico/register', '/API/pico/checkFirmware', '/API/pico/getActionsNeeded',
        '/API/pico/getAssociatedPaks']
    assert d.registered and d.firmware_update_available and d.needs_cleaning
    assert d.paks == [{'rfid': RFID, 'name': 'Test IPA'}]


def test_full_brew_session(fake_server):
    d = make_device(fake_server)
    d.power_on()
    d.insert_pak(RFID)
    d.start_brew()
    run_to_completion(d)

    states = [p['state'] for p in fake_server.paths('/API/pico/picoChangeState')]
    assert states == ['3', '2']

    logs = fake_server.paths('/API/pico/log')
    assert all(l['sesId'] == RFID and l['sesType'] == '0' for l in logs)
    events = [l['event'] for l in logs if 'event' in l]
    assert events == ['Preparing To Brew', 'Heating', 'Dough In', 'Mash 1', 'Mash Out', 'Hops 1', 'Hops 4',
                      'Brewing Complete']
    assert 'complete' in logs[-1]['step'].lower()

    time_left = [int(l['timeLeft']) for l in logs]
    assert time_left == sorted(time_left, reverse=True)
    assert time_left[-1] == 0
    assert max(int(l['wort']) for l in logs) >= 200
    assert d.program is None


def test_unknown_pak(fake_server):
    d = make_device(fake_server)
    d.power_on()
    assert d.insert_pak('ffffffffffffff') is None
    with pytest.raises(CommandError):
        d.start_brew()


def test_pause_location_waits_for_resume(fake_server):
    fake_server.recipes[RFID] = ('Paused', [('Heating', '2', 100, 1, 0), ('Add Grain', '7', 0, 0, 0),
                                            ('Mash', '1', 150, 5, 0)])
    d = make_device(fake_server)
    d.power_on()
    d.insert_pak(RFID)
    d.start_brew()
    for _ in range(50):
        d.tick(10)
    assert d.current_step.name == 'Add Grain' and d.phase == 'waiting'
    d.resume()
    run_to_completion(d)


def test_user_pause_freezes_timer(fake_server):
    d = make_device(fake_server)
    d.power_on()
    d.insert_pak(RFID)
    d.start_brew()
    d.tick(1)
    d.pause()
    step, elapsed = d.step_index, d.phase_elapsed
    for _ in range(20):
        d.tick(10)
    assert (d.step_index, d.phase_elapsed) == (step, elapsed)
    d.resume()
    run_to_completion(d)


def test_deep_clean_uses_session_and_rinse_is_not_logged(fake_server):
    fake_server.actions_needed = '7'
    d = make_device(fake_server)
    d.power_on()
    d.start_deep_clean()
    assert not d.needs_cleaning
    run_to_completion(d)
    logs = fake_server.paths('/API/pico/log')
    assert logs and all(l['sesType'] == '1' and l['sesId'] == '0123456789abcdef0123' for l in logs)

    before = len(logs)
    d.start_rinse()
    run_to_completion(d)
    assert len(fake_server.paths('/API/pico/log')) == before
    assert [p['state'] for p in fake_server.paths('/API/pico/picoChangeState')][-2:] == ['6', '2']


def test_brew_continues_when_server_unreachable(fake_server):
    d = make_device(fake_server)
    d.power_on()
    d.insert_pak(RFID)
    d.start_brew()
    fake_server.offline = True
    d.tick(10)
    assert d.last_comm_error and d.program
    fake_server.offline = False
    run_to_completion(d)
    assert d.last_comm_error is None


def test_abort_returns_to_menu(fake_server):
    d = make_device(fake_server)
    d.power_on()
    d.insert_pak(RFID)
    d.start_brew()
    d.tick(10)
    d.abort()
    assert d.program is None
    assert fake_server.calls[-1][0] == '/API/pico/getAssociatedPaks'


def test_report_error_is_sent_and_logged(fake_server):
    d = make_device(fake_server)
    d.power_on()
    d.insert_pak(RFID)
    d.report_error(5)
    assert fake_server.paths('/API/pico/error')[-1] == {'uid': d.uid, 'code': '5', 'rfid': RFID}
    d.start_brew()
    d.tick(10)
    assert fake_server.paths('/API/pico/log')[-1]['error'] == '5'


def test_manager_persists_devices(tmp_path, fake_server):
    m = DeviceManager(tmp_path, 'http://server', http=fake_server)
    d = m.create('Kitchen', 'pico_pro', uid='abc123')
    with pytest.raises(CommandError):
        m.create('Dup', 'pico_s', uid='abc123')
    d.update_config(speed=30)
    m.save()

    reloaded = DeviceManager(tmp_path, 'http://server', http=fake_server)
    r = reloaded.get(d.id)
    assert (r.name, r.model, r.uid, r.speed) == ('Kitchen', 'pico_pro', 'abc123', 30)
