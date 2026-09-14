#!/usr/bin/env python3
"""
Simulate PicoBrew ecosystem devices (Pico, iSpindel, Tilt, PicoFerm) polling a
running picobrew_pico server over HTTP/JSON, for manual and integration
testing without real hardware.

Run the server first (e.g. `python server.py` -> defaults to port 9999), then
in another shell:

    python scripts/python/device_simulator.py pico
    python scripts/python/device_simulator.py ispindel --readings 20 --live
    python scripts/python/device_simulator.py tilt --color Red --readings 20
    python scripts/python/device_simulator.py ferm --readings 20

Each subcommand registers a fake device (mimicking the '/devices' admin UI
form) and, for the telemetry devices, marks its session 'active' (mimicking
the web UI's start/stop toggle) before sending readings -- iSpindel/Tilt/
PicoFerm telemetry is otherwise silently accepted but never recorded.
"""
import argparse
import json
import random
import string
import sys
import time

import requests

DEFAULT_HOST = 'http://localhost:9999'
TILT_COLORS = ['Red', 'Green', 'Black', 'Purple', 'Orange', 'Blue', 'Yellow', 'Pink']


def random_alnum(n):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))


class DeviceSimulator:
    def __init__(self, host):
        self.host = host.rstrip('/')
        self.session = requests.Session()

    def get(self, path, **params):
        resp = self.session.get(f'{self.host}{path}', params=params)
        resp.raise_for_status()
        return resp

    def post(self, path, **kwargs):
        resp = self.session.post(f'{self.host}{path}', **kwargs)
        resp.raise_for_status()
        return resp

    def register_device(self, machine_type, uid, alias):
        """Mimic the '/devices' admin form: associate a uid with a device type + alias."""
        self.post('/devices', data={'machine_type': machine_type, 'uid': uid, 'alias': alias})

    def set_session_active(self, uid, session_type, active):
        """Mimic the web UI 'start/stop capture' toggle. Required before iSpindel/
        Tilt/PicoFerm telemetry is actually recorded server-side (it's otherwise
        accepted with a 200 and silently discarded)."""
        self.put(f'/device/{uid}/sessions/{session_type}', json={'active': active})

    def put(self, path, **kwargs):
        resp = self.session.put(f'{self.host}{path}', **kwargs)
        resp.raise_for_status()
        return resp


def simulate_pico(sim, args):
    uid = args.uid or random_alnum(32)
    print(f'[pico] uid={uid}')

    if args.register:
        alias = args.alias or f'Sim Pico {uid[:6]}'
        sim.register_device('PicoBrew', uid, alias)
        print(f'[pico] registered machine_type=PicoBrew alias="{alias}"')

    print('[pico] register        ->', sim.get('/API/pico/register', uid=uid).text.strip())
    print('[pico] checkFirmware   ->', sim.get('/API/pico/checkFirmware', uid=uid, version='0.1.34').text.strip())
    print('[pico] getActionsNeeded->', sim.get('/API/pico/getActionsNeeded', uid=uid).text.strip())
    print('[pico] getAssociatedPaks ->', sim.get('/API/pico/getAssociatedPaks', uid=uid).text.strip())

    raw_session_id = sim.get('/API/pico/getSession', uid=uid, sesType=0).text.strip()
    ses_id = raw_session_id.strip('#\r\n') or random_alnum(20)
    print('[pico] getSession      ->', raw_session_id, f'(sesId={ses_id})')

    rfid = args.rfid or random_alnum(14)
    recipe = sim.get('/API/pico/getRecipe', uid=uid, rfid=rfid, ibu=-1, abv=-1).text.strip()
    preview = recipe if len(recipe) <= 80 else recipe[:80] + '...'
    print('[pico] getRecipe       ->', preview)

    steps = ['Preheating', 'Dough In', 'Mash', 'Boil', 'Cool Down', 'Complete']
    for i, step in enumerate(steps):
        time_left = (len(steps) - i - 1) * 60
        wort = 150 + i * 10
        therm = 148 + i * 10
        resp = sim.get('/API/pico/log', uid=uid, sesId=ses_id, wort=wort, therm=therm,
                        step=step, event=step, error=0, sesType=0, timeLeft=time_left,
                        shutScale=0.0)
        print(f'[pico] log step="{step}" timeLeft={time_left}s ->', resp.text.strip() or '(empty)')
        if i < len(steps) - 1:
            time.sleep(args.interval)

    print('[pico] simulated brew session complete.')


def simulate_ispindel(sim, args):
    uid = args.uid or str(random.randint(10000, 99999))
    alias = args.alias or f'Sim iSpindel {uid}'
    print(f'[ispindel] ID={uid}')

    if args.register:
        sim.register_device('iSpindel', uid, alias)
        sim.set_session_active(uid, 'iSpindel', True)
        print(f'[ispindel] registered alias="{alias}" and started capture')

    gravity = args.start_gravity
    for i in range(args.readings):
        payload = {
            'name': alias,
            'ID': int(uid),
            'angle': round(random.uniform(20, 30), 2),
            'temperature': round(args.temp + random.uniform(-0.5, 0.5), 2),
            'temp_units': 'F',
            'battery': round(random.uniform(3.9, 4.2), 2),
            'gravity': round(gravity, 4),
            'interval': int(args.interval),
            'RSSI': random.randint(-80, -40),
        }
        resp = sim.post('/API/iSpindel', json=payload)
        print(f'[ispindel] reading {i + 1}/{args.readings} gravity={payload["gravity"]} '
              f'temp={payload["temperature"]}F -> {resp.status_code}')
        gravity = max(1.000, gravity - args.gravity_step)
        if i < args.readings - 1:
            time.sleep(args.interval if args.live else 0.2)

    if args.register and not args.keep_active:
        sim.set_session_active(uid, 'iSpindel', False)
        print('[ispindel] capture stopped')


def simulate_tilt(sim, args):
    uid = args.uid or random_alnum(12)
    alias = args.alias or f'Sim Tilt {args.color}'
    print(f'[tilt] uid={uid} color={args.color}')

    if args.register:
        sim.register_device('Tilt', uid, alias)
        sim.set_session_active(uid, 'tilt', True)
        print(f'[tilt] registered alias="{alias}" and started capture')

    gravity = args.start_gravity
    for i in range(args.readings):
        reading = {
            'color': args.color,
            'uid': uid,
            'temp': round(args.temp_c + random.uniform(-0.3, 0.3), 2),  # Celsius; server converts to F
            'gravity': int(round(gravity * 1000)),  # low-res raw int, e.g. 1060 == SG 1.060
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'rssi': random.randint(-80, -40),
        }
        resp = sim.post('/API/tilt', json=[reading])
        print(f'[tilt] reading {i + 1}/{args.readings} sg={gravity:.3f} '
              f'temp={reading["temp"]}C -> {resp.status_code}')
        gravity = max(1.000, gravity - args.gravity_step)
        if i < args.readings - 1:
            time.sleep(args.interval if args.live else 0.2)

    if args.register and not args.keep_active:
        sim.set_session_active(uid, 'tilt', False)
        print('[tilt] capture stopped')


def simulate_ferm(sim, args):
    uid = args.uid or random_alnum(12)
    alias = args.alias or f'Sim PicoFerm {uid[:6]}'
    print(f'[ferm] uid={uid}')

    if args.register:
        sim.register_device('PicoFerm', uid, alias)
        sim.set_session_active(uid, 'ferm', True)
        print(f'[ferm] registered alias="{alias}" and started capture')

    print('[ferm] isRegistered   ->', sim.get('/API/PicoFerm/isRegistered', uid=uid, token=random_alnum(8)).text.strip())
    print('[ferm] checkFirmware  ->', sim.get('/API/PicoFerm/checkFirmware', uid=uid, version='0.2.6').text.strip())

    for i in range(args.readings):
        state = sim.get('/API/PicoFerm/getState', uid=uid).text.strip()
        samples = [{'s1': round(args.temp + random.uniform(-0.5, 0.5), 2),
                    's2': round(args.pressure + random.uniform(-0.2, 0.2), 2)}
                   for _ in range(args.samples_per_batch)]
        resp = sim.get('/API/PicoFerm/logDataSet', uid=uid, rate=args.rate,
                        voltage=round(random.uniform(3.9, 4.2), 2), data=json.dumps(samples))
        print(f'[ferm] reading batch {i + 1}/{args.readings} state={state} samples={samples} -> {resp.text.strip()}')
        if i < args.readings - 1:
            time.sleep(args.interval if args.live else 0.2)

    if args.register and not args.keep_active:
        sim.set_session_active(uid, 'ferm', False)
        print('[ferm] capture stopped')


def add_common_args(parser):
    parser.add_argument('--uid', help='Device UID/ProductID to simulate (random if omitted)')
    parser.add_argument('--alias', help='Friendly name shown in the picobrew_pico UI')
    parser.add_argument('--no-register', dest='register', action='store_false',
                         help="Don't auto-register the device via /devices or start its session capture first")
    parser.set_defaults(register=True)


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default=DEFAULT_HOST,
                         help='Base URL of the running picobrew_pico server (default: %(default)s)')
    sub = parser.add_subparsers(dest='device', required=True)

    pico = sub.add_parser('pico', help='Simulate a Pico brewing a session '
                                        '(register -> recipe -> stepped log() calls)')
    add_common_args(pico)
    pico.add_argument('--rfid', help='PicoPak RFID to "scan" (random if omitted)')
    pico.add_argument('--interval', type=float, default=1.0,
                       help='Seconds to sleep between simulated brew steps (default: %(default)s)')

    ispindel = sub.add_parser('ispindel', help='Simulate an iSpindel posting gravity/temp readings')
    add_common_args(ispindel)
    ispindel.add_argument('--readings', type=int, default=10)
    ispindel.add_argument('--interval', type=float, default=60.0,
                           help='Sampling interval in seconds, reported to the server and (with --live) '
                                'actually slept between readings (default: %(default)s)')
    ispindel.add_argument('--live', action='store_true',
                           help='Sleep the full --interval between readings instead of sending them back-to-back')
    ispindel.add_argument('--start-gravity', type=float, default=1.060)
    ispindel.add_argument('--gravity-step', type=float, default=0.004, help='Gravity drop per reading')
    ispindel.add_argument('--temp', type=float, default=68.0, help='Fahrenheit')
    ispindel.add_argument('--keep-active', action='store_true',
                           help="Don't mark the session inactive after the last reading")

    tilt = sub.add_parser('tilt', help='Simulate a Tilt hydrometer posting readings')
    add_common_args(tilt)
    tilt.add_argument('--color', default='Red', choices=TILT_COLORS)
    tilt.add_argument('--readings', type=int, default=10)
    tilt.add_argument('--interval', type=float, default=60.0)
    tilt.add_argument('--live', action='store_true')
    tilt.add_argument('--start-gravity', type=float, default=1.060)
    tilt.add_argument('--gravity-step', type=float, default=0.004)
    tilt.add_argument('--temp-c', type=float, default=20.0, help='Celsius (server converts to F)')
    tilt.add_argument('--keep-active', action='store_true')

    ferm = sub.add_parser('ferm', help='Simulate a PicoFerm posting temperature/pressure readings')
    add_common_args(ferm)
    ferm.add_argument('--readings', type=int, default=10)
    ferm.add_argument('--rate', type=float, default=1.0, help='Minutes between samples, reported to the server')
    ferm.add_argument('--interval', type=float, default=60.0,
                       help='Seconds to actually sleep between logDataSet batches when --live is set')
    ferm.add_argument('--live', action='store_true')
    ferm.add_argument('--samples-per-batch', type=int, default=1)
    ferm.add_argument('--temp', type=float, default=68.0, help='Fahrenheit')
    ferm.add_argument('--pressure', type=float, default=14.7, help='PSI')
    ferm.add_argument('--keep-active', action='store_true')

    return parser


SIMULATORS = {
    'pico': simulate_pico,
    'ispindel': simulate_ispindel,
    'tilt': simulate_tilt,
    'ferm': simulate_ferm,
}


def main():
    args = build_parser().parse_args()
    sim = DeviceSimulator(args.host)
    try:
        SIMULATORS[args.device](sim, args)
    except KeyboardInterrupt:
        print('\nInterrupted.')
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print(f"Couldn't connect to {args.host} -- is the picobrew_pico server running? (python server.py)")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f'Server returned an error: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
