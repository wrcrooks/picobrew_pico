from flask import Blueprint, current_app, jsonify, render_template, request

from .devices import MODELS, CommandError, DeviceNotFound

bp = Blueprint('sim', __name__)


def manager():
    return current_app.config['DEVICE_MANAGER']


def supported(device, method):
    fn = getattr(device, method, None)
    if fn is None:
        raise CommandError(f'{MODELS[device.model]["label"]} does not support {method.replace("_", " ")}')
    return fn


COMMANDS = {
    'power_on': lambda d, b: d.power_on(),
    'power_off': lambda d, b: d.power_off(),
    'reconnect': lambda d, b: d.handshake(),
    'refresh_paks': lambda d, b: supported(d, 'refresh_paks')(),
    'insert_pak': lambda d, b: supported(d, 'insert_pak')(b.get('rfid')),
    'eject_pak': lambda d, b: supported(d, 'eject_pak')(),
    'refresh_menu': lambda d, b: supported(d, 'refresh_menu')(),
    'select_recipe': lambda d, b: supported(d, 'select_recipe')(b.get('recipe_id')),
    'start_brew': lambda d, b: d.start_brew(),
    'start_program': lambda d, b: d.start_program(b.get('program')),
    'start_sous_vide': lambda d, b: supported(d, 'start_sous_vide')(b.get('temperature'), b.get('minutes')),
    'resume_session': lambda d, b: supported(d, 'resume_session')(),
    'pause': lambda d, b: d.pause(),
    'resume': lambda d, b: d.resume(),
    'skip_step': lambda d, b: d.skip_step(),
    'abort': lambda d, b: d.abort(),
    'report_error': lambda d, b: d.report_error(b.get('code')),
    'clear_error': lambda d, b: d.clear_error(),
    'check_firmware': lambda d, b: d.check_firmware(),
    'download_firmware': lambda d, b: supported(d, 'download_firmware')(),
    'register_alias': lambda d, b: d.register_alias(),
}


@bp.errorhandler(DeviceNotFound)
def not_found(e):
    return jsonify({'error': f'No such device: {e.args[0]}'}), 404


@bp.errorhandler(CommandError)
def command_error(e):
    return jsonify({'error': str(e)}), 400


@bp.errorhandler(ValueError)
@bp.errorhandler(TypeError)
def bad_value(e):
    return jsonify({'error': f'Invalid value: {e}'}), 400


@bp.route('/')
def index():
    return render_template('index.html', models=MODELS, default_server_url=manager().default_server_url)


@bp.route('/api/devices')
def list_devices():
    return jsonify([{
        **d.config(),
        'model_label': MODELS[d.model]['label'],
        'powered': d.powered,
        'program': d.program.label if d.program else None,
    } for d in manager().devices.values()])


@bp.route('/api/devices', methods=['POST'])
def create_device():
    body = request.get_json(force=True) or {}
    device = manager().create(body.get('name'), body.get('model') or 'pico_s', body.get('uid'),
                              body.get('firmware'), body.get('server_url'))
    return jsonify(device.snapshot()), 201


@bp.route('/api/devices/<device_id>')
def get_device(device_id):
    since = request.args.get('history_since', type=float)
    return jsonify(manager().get(device_id).snapshot(history_since=since))


@bp.route('/api/devices/<device_id>', methods=['PATCH'])
def update_device(device_id):
    body = request.get_json(force=True) or {}
    device = manager().get(device_id)
    device.update_config(name=body.get('name'), firmware=body.get('firmware'),
                         server_url=body.get('server_url'), speed=body.get('speed'))
    manager().save()
    return jsonify(device.snapshot())


@bp.route('/api/devices/<device_id>', methods=['DELETE'])
def delete_device(device_id):
    manager().delete(device_id)
    return '', 204


@bp.route('/api/devices/<device_id>/command', methods=['POST'])
def run_command(device_id):
    body = request.get_json(force=True) or {}
    command = COMMANDS.get(body.get('command'))
    if not command:
        raise CommandError(f'Unknown command {body.get("command")!r}')
    device = manager().get(device_id)
    command(device, body)
    return jsonify(device.snapshot())
