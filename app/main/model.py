from flask import current_app
import requests
import shutil
import json

from .config import (MachineType, brew_archive_sessions_path, ferm_archive_sessions_path,
                     still_archive_sessions_path, iSpindel_archive_sessions_path, tilt_archive_sessions_path)

ZYMATIC_LOCATION = {
    'PassThru': '0',
    'Mash': '1',
    'Adjunct1': '2',
    'Adjunct2': '3',
    'Adjunct3': '4',
    'Adjunct4': '5',
    'Pause': '6',
}

ZSERIES_LOCATION = {
    'PassThru': '0',
    'Mash': '1',
    'Adjunct1': '2',
    'Adjunct2': '3',
    'Adjunct3': '4',
    'Adjunct4': '5',
    'Pause': '6',
}

PICO_LOCATION = {
    'Prime': '0',
    'Mash': '1',
    'PassThru': '2',
    'Adjunct1': '3',
    'Adjunct2': '4',
    'Adjunct3': '6',
    'Adjunct4': '5',
}

PICO_SESSION = {
    0: 'Brewing',
    1: 'Deep Clean',
    2: 'Sous Vide',
    4: 'Cold Brew',
    5: 'Manual Brew',
}

SRM_COLOR_DATA = {
    1: {'Name': 'Pale Transparent', 'HEX': '#F7E1A1'},
    2: {'Name': 'Pale Straw', 'HEX': '#F0C566'},
    3: {'Name': 'Straw', 'HEX': '#E9AD3F'},
    4: {'Name': 'Pale Gold', 'HEX': '#E19726'},
    5: {'Name': 'Golden', 'HEX': '#D98416'},
    6: {'Name': 'Deep Gold / Dark Goldenrod', 'HEX': '#D1730C'},
    7: {'Name': 'Light Amber', 'HEX': '#C86505'},
    8: {'Name': 'Amber', 'HEX': '#C05801'},
    9: {'Name': 'Medium Amber', 'HEX': '#B74D00'},
    10: {'Name': 'Copper / Orange', 'HEX': '#AF4300'},
    11: {'Name': 'Deep Copper', 'HEX': '#A73B00'},
    12: {'Name': 'Deep Amber / Ruby', 'HEX': '#9F3400'},
    13: {'Name': 'Dark Ruby', 'HEX': '#972D00'},
    14: {'Name': 'Reddish Brown', 'HEX': '#8F2800'},
    15: {'Name': 'Mahogany / Deep Red', 'HEX': '#882300'},
    16: {'Name': 'Maroon', 'HEX': '#811F00'},
    17: {'Name': 'Dark Brown / Pueblo', 'HEX': '#7B1B00'},
    18: {'Name': 'Brown', 'HEX': '#741800'},
    19: {'Name': 'Deep Brown / Barn Red', 'HEX': '#6E1500'},
    20: {'Name': 'Rosewood / Very Dark Brown', 'HEX': '#681200'},
    21: {'Name': 'Black Brown', 'HEX': '#631000'},
    22: {'Name': 'Red Oxide', 'HEX': '#5E0E00'},
    23: {'Name': 'Rustic Red', 'HEX': '#590C00'},
    24: {'Name': 'Burnt Maroon', 'HEX': '#540B00'},
    25: {'Name': 'Pheasant Red', 'HEX': '#500900'},
    26: {'Name': 'Brown Pod', 'HEX': '#4C0800'},
    27: {'Name': 'Temptress', 'HEX': '#480700'},
    28: {'Name': 'Very Dark Red', 'HEX': '#450600'},
    29: {'Name': 'Near Black', 'HEX': '#420500'},
    30: {'Name': 'Black (Ruby highlights)', 'HEX': '#3F0500'},
    35: {'Name': 'Black', 'HEX': '#310400'},
    40: {'Name': 'Black (Opaque)', 'HEX': '#260300'},
    45: {'Name': 'Opaque Black', 'HEX': '#1C0200'},
    50: {'Name': 'Pitch Black', 'HEX': '#120100'},
    55: {'Name': 'Very Deep Black', 'HEX': '#090100'},
    60: {'Name': 'Stygian/Deepest Black', 'HEX': '#000000'}
}

class PicoBrewSession:
    def __init__(self, machineType=None):
        self.file = None
        self.filepath = None
        self.alias = ''
        self.machine_type = machineType
        self.created_at = None
        self.name = 'Waiting To Brew'
        self.type = 0
        self.step = ''
        self.session = ''   # session guid
        self.id = -1        # session id (integer)
        self.recovery = ''
        self.remaining_time = None
        self.is_pico = True if machineType in [MachineType.PICOBREW, MachineType.PICOBREW_C, MachineType.PICOBREW_C_ALT] else False
        self.has_alt_firmware = True if machineType in [MachineType.PICOBREW_C_ALT] else False
        self.needs_firmware = False
        self.boiler_type = None   # Z machines have 2 different configurations: 1 (big) or 2 (small)
        self.data = []

    def cleanup(self):
        if self.file and self.filepath:
            self.file.close()
            shutil.move(str(self.filepath), str(brew_archive_sessions_path()))
        self.file = None
        self.filepath = None
        self.created_at = None
        self.name = 'Waiting To Brew'
        self.type = 0
        self.step = ''
        self.session = ''
        self.id = -1
        self.recovery = ''
        self.remaining_time = None
        self.data = []


class PicoStillSession:
    def __init__(self, uid=None):
        self.file = None
        self.filepath = None
        self.alias = ''
        self.ip_address = None
        self.device_id = uid
        self.uninit = True
        self.created_at = None
        self.name = 'Waiting To Distill'
        self.active = False
        self.session = ''   # session guid
        self.polling_thread = None
        self.data = []

    def cleanup(self):
        if self.file and self.filepath:
            self.file.close()
            shutil.move(str(self.filepath), str(still_archive_sessions_path()))
        self.file = None
        self.filepath = None
        self.uninit = True
        self.created_at = None
        self.name = 'Waiting To Distill'
        self.active = False
        self.polling_thread = None
        self.session = ''
        self.data = []

    def start_still_polling(self):
        connect_failure = False
        failure_message = None
        still_data_uri = 'http://{}/data'.format(self.ip_address)
        try:
            current_app.logger.debug('DEBUG: Retrieve PicoStill Data - {}'.format(still_data_uri))
            r = requests.get(still_data_uri)
            datastring = r.text.strip()
        except Exception as e:
            current_app.logger.error(f'exception occured communicating to picostill {still_data_uri} : {e}')
            failure_message = f'unable to estaablish successful connection to {still_data_uri}'
            datastring = None
            connect_failure = True

        if not datastring or datastring[0] != '#':
            connect_failure = True
            failure_message = f'received unexpected response string from {still_data_uri}'
            current_app.logger.error(f'{failure_message} : {datastring}')

        if connect_failure:
            raise Exception(f'Failed to Start PicoStill Monitoring: {failure_message}')

        from .still_polling import new_still_session
        from .still_polling import FlaskThread

        thread = FlaskThread(target=new_still_session,
                             args=(self.ip_address, self.device_id),
                             daemon=True)
        thread.start()
        self.polling_thread = thread


class PicoFermSession:
    def __init__(self):
        self.file = None
        self.filepath = None
        self.alias = ''
        self.active = False
        self.uninit = True
        self.voltage = '-'
        self.start_time = None
        self.data = []

    def cleanup(self):
        if self.file and self.filepath:
            self.file.close()
            shutil.move(str(self.filepath), str(ferm_archive_sessions_path()))
        self.file = None
        self.filepath = None
        self.uninit = True
        self.voltage = '-'
        self.start_time = None
        self.data = []


class iSpindelSession:
    def __init__(self):
        self.file = None
        self.filepath = None
        self.alias = ''
        self.active = False
        self.uninit = True
        self.voltage = '-'
        self.start_time = None
        self.data = []

    def cleanup(self):
        if self.file and self.filepath:
            self.file.close()
            shutil.move(str(self.filepath), str(
                iSpindel_archive_sessions_path()))
        self.file = None
        self.filepath = None
        self.uninit = True
        self.voltage = '-'
        self.start_time = None
        self.data = []


class TiltSession:
    def __init__(self):
        self.file = None
        self.filepath = None
        self.alias = ''
        self.color = None
        self.active = False
        self.uninit = True
        self.rssi = None
        self.start_time = None
        self.data = []

    def cleanup(self):
        if self.file and self.filepath:
            self.file.close()
            shutil.move(str(self.filepath), str(
                tilt_archive_sessions_path()))
        self.file = None
        self.filepath = None
        self.uninit = True
        self.rssi = None
        self.start_time = None
        self.data = []


class SupportObject:
    def __init__(self):
        self.name = None
        self.logo = None
        self.manual = None
        self.faq = None
        self.instructional_videos = None
        self.misc_media = None

    def toJSON(self):
        return json.dumps(self, default=lambda o: o.__dict__, indent=4)


class SupportMedia:
    def __init__(self, path, owner="Picobrew"):
        self.path = path
        self.owner = owner
