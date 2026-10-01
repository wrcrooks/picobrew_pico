import json
import os
import uuid
from collections import deque
from datetime import datetime, timezone
from flask import current_app, jsonify, request
from webargs import fields
from webargs.flaskparser import use_args, FlaskParser

from .. import socketio
from . import main
from .config import MachineType, brew_active_sessions_path, firmware_path
from .firmware import firmware_filename, minimum_firmware, firmware_upgrade_required
from .model import PicoBrewSession, PICO_SESSION
from .routes_frontend import get_pico_recipes, load_redux_recipes, find_redux_recipe_by_tag_id
from .session_parser import active_brew_sessions, dirty_sessions_since_clean
from .mqtt import parse_and_send


arg_parser = FlaskParser()


# In-memory log of the most recent NFC/RFID tag scans -- i.e. every getRecipe/error hit that
# carries an rfid, which is the only moment a physical tag scan reaches this server. Powers
# the Tag Scanner browser tool (/scanner): there's no other way to see which physical tag a
# recipe is tied to, or what an unlabeled/mystery tag resolves to, short of reading server
# logs. Newest first, capped so it can't grow unbounded across a long-running server.
recent_tag_scans = deque(maxlen=100)


def record_tag_scan(uid, rfid, recipe_name=None, found=False, event='scan'):
    record = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'uid': uid,
        'rfid': rfid,
        'recipeName': recipe_name,
        'found': found,
        'event': event,
    }
    recent_tag_scans.appendleft(record)
    socketio.emit('tag_scanned', json.dumps(record))
    return record


# Register: /API/pico/register?uid={UID}
# Response: '#{0}#\r\n' where {0} : T = Registered, F = Not Registered
register_args = {
    'uid': fields.Str(required=True),       # 32 character alpha-numeric serial number
}


@main.route('/API/pico/register')
@use_args(register_args, location='querystring')
def process_register(args):
    uid = args['uid']
    if uid not in active_brew_sessions:
        active_brew_sessions[uid] = PicoBrewSession()
    parse_and_send(1, uid, args)
    return '#T#\r\n'


# Change State: /API/pico/picoChangeState?picoUID={UID}&state={STATE}
#     Response: '\r\n'
change_state_args = {
    'picoUID': fields.Str(required=True),   # 32 character alpha-numeric serial number
    'state': fields.Int(required=True),     # 2 = Ready, 3 = Brewing, 4 = Sous Vide, 5 = Rack Beer, 6 = Rinse, 7 = Deep Clean, 9 = De-Scale
}


@main.route('/API/pico/picoChangeState')
@use_args(change_state_args, location='querystring')
def process_change_state_request(args):
    parse_and_send(4, args['picoUID'], json.dumps(args), str(args['picoUID']) + "/state")
    return '\r\n'


# Check Firmware: /API/pico/checkFirmware?uid={UID}&version={VERSION}
#       Response: '#{0}#' where {0} : T = Update Available, F = No Updates
check_firmware_args = {
    'uid': fields.Str(required=True),       # 32 character alpha-numeric serial number
    'version': fields.Str(required=True),   # Current firmware version - i.e. 0.1.11
}


@main.route('/API/pico/checkFirmware')
@use_args(check_firmware_args, location='querystring')
def process_check_firmware(args):
    uid = args['uid']
    # only give update available if machine type is known (C firmware != S/Pro Firmware)
    if uid in active_brew_sessions:
        active_session = active_brew_sessions[uid]
        if active_session.needs_firmware:
            return '#T#'
        if active_session.machine_type and firmware_upgrade_required(active_session.machine_type, args['version']):
            return '#T#'
    return '#F#'


# Get Firmware: /API/pico/getFirmware?uid={UID}
#     Response: RAW Bin File Contents
get_firmware_args = {
    'uid': fields.Str(required=True),       # 32 character alpha-numeric serial number
}


@main.route('/API/pico/getFirmware')
@use_args(get_firmware_args, location='querystring')
def process_get_firmware(args):
    uid = args['uid']
    if uid in active_brew_sessions and active_brew_sessions[uid].machine_type is not None:
        machine_type = active_brew_sessions[uid].machine_type
        filename = firmware_filename(machine_type, minimum_firmware(machine_type))
        f = open(firmware_path(machine_type).joinpath(filename))
        fw = f.read()
        f.close()
        return '{}'.format(fw)
    else:
        current_app.logger.warning(active_brew_sessions)
        current_app.logger.warning('machine_type unknown - can not fetch firmware. Configuration of the device type via /devices UX is required.')
        # TODO: Error Processing?
        return '#F#'


# Actions Needed: /API/pico/getActionsNeeded?uid={UID}
#       Response: '#{0}#' where {0} : Empty = None, 7 = Deep Clean
actions_needed_args = {
    'uid': fields.Str(required=True),       # 32 character alpha-numeric serial number
}


@main.route('/API/pico/getActionsNeeded')
@use_args(actions_needed_args, location='querystring')
def process_get_actions_needed(args):
    if dirty_sessions_since_clean(args['uid'], MachineType.PICOBREW) >= 3:
        args['actionNeeded'] = 'Machine requires cleaning'
        parse_and_send(json.dumps(args), str(args['uid']) + "/error")
        return '#7#'
    return '##'


#    Error: /API/pico/error?uid={UID}&rfid={RFID}
# Response: '\r\n'
error_args = {
    'uid': fields.Str(required=True),        # 32 character alpha-numeric serial number
    'code': fields.Str(required=True),       # Integer error number
    'rfid': fields.Str(required=False),      # 14 character alpha-numeric PicoPak RFID (could be blank)
}


@main.route('/API/pico/error')
@use_args(error_args, location='querystring')
def process_error(args):
    # TODO: Error Processing?
    if args.get('rfid'):
        record_tag_scan(args['uid'], args['rfid'], found=False, event='error ({})'.format(args.get('code')))
    parse_and_send(json.dumps(args), str(args['uid']) + "/error")
    return '\r\n'


# Get Session: /API/pico/getSession?uid={UID}&sesType={SESSION_TYPE}
#    Response: '#{0}#\r\n' where {0} : 20 character alpha-numeric session id
get_session_args = {
    'uid': fields.Str(required=True),       # 32 character alpha-numeric serial number
    'sesType': fields.Int(required=True),   # 0 = Brewing (never happens since session = 14 alpha-numeric RFID), 1 = Deep Clean, 2 = Sous Vide, 4 = Cold Brew, 5 = Manual Brew
}


@main.route('/API/pico/getSession')
@use_args(get_session_args, location='querystring')
def process_get_session(args):
    cleanup_old_session(args['uid'])
    return '#{0}#\r\n'.format(uuid.uuid4().hex[:20])


# Recipe List: /API/pico/recipelist?uid={UID}
#    Response: '#{0}#\r\n\r\n' where {0} : ??
recipe_list_args = {
    'uid': fields.Str(required=True),       # 32 character alpha-numeric serial number
}


@main.route('/API/pico/recipelist')
@use_args(recipe_list_args, location='querystring')
def process_recipe_list(args):
    # TODO: Never Captured with an actual recipe list
    return '##\r\n\r\n'


# Associated Paks: /API/pico/getAssociatedPaks?uid={UID}
#        Response: '\r\n#{0}#\r\n\r\n' where {0} : [14 character alpha-numeric PicoPak Tag,Name|]+
associated_paks_args = {
    'uid': fields.Str(required=True),       # 32 character alpha-numeric serial number
}


@main.route('/API/pico/getAssociatedPaks')
@use_args(associated_paks_args, location='querystring')
def process_associated_paks(args):
    cleanup_old_session(args['uid'])
    return '\r\n#{0}#\r\n\r\n'.format(get_recipe_list())


#   Recipe: /API/pico/getRecipe?uid={UID}&rfid={RFID}&ibu={IBU}&abv={ABV}
# Response: '{0}' where : #NAME/IBU_TWEAK,ABV_TWEAK,ABV,IBU,[TEMPERATURE,STEP_TIME,DRAIN_TIME,LOCATION,STEP_NAME]+,|128x64 1024 byte OLED Image|#
get_recipe_args = {
    'uid': fields.Str(required=True),       # 32 character alpha-numeric serial number
    'rfid': fields.Str(required=True),      # 14 character alpha-numeric PicoPak RFID
    'ibu': fields.Str(required=True),       # Decimal IBU Tweak (i.e. -1, ignore -> not supported)
    'abv': fields.Str(required=True),       # Decimal ABV Tweak (i.e. -1.0, ignore -> not supported)
}


@main.route('/API/pico/getRecipe')
@use_args(get_recipe_args, location='querystring')
def process_get_recipe(args):
    # TODO: figure out what to do with IBU/ABV tweaks
    args['recipeName'] = get_recipe_name_by_id(args['rfid'])
    record_tag_scan(args['uid'], args['rfid'], args['recipeName'], found=args['recipeName'] != 'Invalid Recipe')
    parse_and_send(2, args['uid'], json.dumps(args), str(args['uid']) + "/RecipeLoaded")
    return '#{0}#'.format(get_recipe_by_id(args['rfid']))


#       Log: /API/pico/log?uid={UID}&sesId={SID}&wort={TEMP}&therm={TEMP}&step={STEP_NAME}&[event={STEP_NAME}&]error={ERROR}&sesType={SESSION_TYPE}&timeLeft={TIME}&shutScale={SS}
#  Response: '\r\n\r\n'
log_args = {
    'uid': fields.Str(required=True),          # 32 character alpha-numeric serial number
    'sesId': fields.Str(required=True),        # 14/20 character alphanumeric session id
    'wort': fields.Int(required=True),         # Integer Temperature
    'therm': fields.Int(required=True),        # Integer Temperature
    'step': fields.Str(required=True),         # HTTP formatted step name (Preparing%20to%20Brew)
    'event': fields.Str(required=False),       # HTTP formatted step name (Preparing%20to%20Brew) : only occurs when new steps start
    'error': fields.Int(required=True),        # Integer error number
    'sesType': fields.Int(required=True),      # 0 = Brewing, 1 = Deep Clean, 2 = Sous Vide
    'timeLeft': fields.Int(required=True),     # Integer (Seconds Left?)
    'shutScale': fields.Float(required=True),  # %0.2f
}


@main.route('/API/pico/log')
@use_args(log_args, location='querystring')
def process_log(args):
    uid = args['uid']
    if uid not in active_brew_sessions or active_brew_sessions[uid].name == 'Waiting To Brew':
        create_new_session(uid, args['sesId'], args['sesType'])
    aware_naive_dt = datetime(1970, 1, 1).replace(tzinfo=timezone.utc)
    session_data = {'time': ((datetime.now(tz=timezone.utc) - aware_naive_dt).total_seconds() * 1000),
                    'timeLeft': args['timeLeft'],
                    'step': args['step'],
                    'wort': args['wort'],
                    'therm': args['therm'],
                    }
    event = None
    if 'event' in args:
        event = args['event']
        session_data.update({'event': event})
    active_brew_sessions[uid].step = args['step']
    active_brew_sessions[uid].data.append(session_data)
    graph_update = json.dumps({'time': session_data['time'],
                               'data': [session_data['wort'], session_data['therm']],
                               'session': active_brew_sessions[uid].name,
                               'step': active_brew_sessions[uid].step,
                               'event': event,
                               })
    socketio.emit('brew_session_update|{}'.format(uid), graph_update)
    if 'complete' in active_brew_sessions[uid].step.lower():
        active_brew_sessions[uid].file.write('\n\t{}\n]'.format(json.dumps(session_data)))
        active_brew_sessions[uid].cleanup()
    else:
        active_brew_sessions[uid].file.write('\n\t{},'.format(json.dumps(session_data)))
        active_brew_sessions[uid].file.flush()
    parse_and_send(3, uid, json.dumps(args), str(args['uid']) + "/log")
    return '\r\n\r\n'


# Pico with Picostill requests
pico_still_args = {
    'picoUid': fields.Str(required=True),       # 32 character alpha-numeric serial number
    'picoStillUid': fields.Str(required=True),   # 12 character alpha-numeric serial number
}
# Can Use Still: /API/pico/canUsePicoStill?picoUid={UID}&picoStillUid={UID}
#    Response: '#{0}#\r\n' where {0} : T (or F?)


@main.route('/API/pico/canUsePicoStill')
@use_args(pico_still_args, location='querystring')
def process_can_use_pico_still(args):
    return '#T#\r\n'


# Has cleaned still: /API/pico/hasCleanedAck?picoUid={UID}&picoStillUid={UID}
#    Response: '#{0}#\r\n' where {0} : T (or F?)
@main.route('/API/pico/hasCleanedAck')
@use_args(pico_still_args, location='querystring')
def process_is_cleaned(args):
    return '#T#\r\n'


# Set cleaned still: /API/pico/setCleanedAck?picoUid={UID}&picoStillUid={UID}
#    Response: '#T#\r\n'
# Allows a user override of hasCleanedAck if unsuccessful
@main.route('/API/pico/setCleanedAck')
@use_args(pico_still_args, location='querystring')
def process_set_cleaned(args):
    return '#T#\r\n'


# -------- Utility --------
# A real Pico's getRecipe/getAssociatedPaks scans only ever carry a 14-character tag ID, so
# these three all fall back to a unified recipe carrying that same TagID (assigned in
# recipe_editor.html's Tag Programming section) when no legacy recipe matches -- letting a
# unified recipe actually be brewed via a physical NFC scan, not just identified by the Tag
# Scanner tool. Active-only (include_archived=False), matching get_pico_recipes(False)'s own
# "only what a real Pico may currently be served" semantics.
def get_recipe_by_id(recipe_id):
    recipe = next((r for r in get_pico_recipes(False) if r.id == recipe_id), None)
    if recipe:
        return recipe.serialize()
    redux_recipe = find_redux_recipe_by_tag_id(recipe_id, include_archived=False)
    return redux_recipe.to_legacy_pico_recipe().serialize() if redux_recipe else ''


def get_recipe_name_by_id(recipe_id):
    recipe = next((r for r in get_pico_recipes(False) if r.id == recipe_id), None)
    if recipe:
        return recipe.name
    redux_recipe = find_redux_recipe_by_tag_id(recipe_id, include_archived=False)
    return redux_recipe.name if redux_recipe else 'Invalid Recipe'


def get_recipe_list():
    recipe_list = ''
    for r in get_pico_recipes(False):
        recipe_list += f'{r.id},{r.name}|'
    for r in load_redux_recipes(False):
        if r.TagID:
            recipe_list += f'{r.TagID},{r.name}|'
    return recipe_list


# -------- Tag Scanner browser tool (see app/templates/scanner.html) --------
# Recipes searched here come from two independent stores that are mid-migration: legacy
# PicoBrewRecipe files (app/recipes/pico/*.json, viewed at /pico_recipes) and the newer
# unified/Redux store (app/recipes/unified/*.json, viewed/created at /recipes and edited at
# /recipe/edit/<id> -- what /pico_recipes is being retired in favor of). A unified recipe's
# 'id' is its 32-character RecipeGUID (used to build its edit-page link), which is NOT what
# goes on a physical tag; its optional 'TagID' (set in the editor's Tag Programming section,
# and what get_recipe_by_id/get_recipe_name_by_id above actually serve to a real Pico) is the
# 14-character value that does, so 'tagId' below is what the browser tool should actually
# show/match as a tag -- None until the recipe's had one assigned.
def _recipe_summary(recipe, source):
    return {
        'id': recipe.id,
        'name': recipe.name,
        'abv': recipe.abv,
        'ibu': recipe.ibu,
        'is_archived': recipe.is_archived,
        'source': source,
        'tagId': recipe.id if source == 'pico' else getattr(recipe, 'TagID', None),
    }


def find_recipe_by_rfid(rfid):
    # Include archived recipes -- a tag pointing at an archived recipe is still a useful,
    # identifiable answer for this tool, unlike the machine-facing endpoints above which must
    # only ever hand a real Pico its currently active recipes.
    recipe = next((r for r in get_pico_recipes(True) if r.id == rfid), None)
    if recipe:
        return _recipe_summary(recipe, 'pico')
    recipe = find_redux_recipe_by_tag_id(rfid, include_archived=True)
    return _recipe_summary(recipe, 'unified') if recipe else None


def find_recipes_by_name(query):
    query = query.strip().lower()
    if not query:
        return []
    matches = [_recipe_summary(r, 'pico') for r in get_pico_recipes(True) if query in (r.name or '').lower()]
    matches += [_recipe_summary(r, 'unified') for r in load_redux_recipes() if query in (r.name or '').lower()]
    matches.sort(key=lambda r: r['name'].lower())
    return matches[:25]


@main.route('/API/scanner/recent')
def process_scanner_recent():
    return jsonify(list(recent_tag_scans))


scanner_lookup_args = {
    'rfid': fields.Str(required=True),
}


@main.route('/API/scanner/lookup')
@use_args(scanner_lookup_args, location='querystring')
def process_scanner_lookup(args):
    rfid = args['rfid'].strip()
    recipe = find_recipe_by_rfid(rfid)
    return jsonify({'rfid': rfid, 'found': recipe is not None, 'recipe': recipe})


@main.route('/API/scanner/search_recipe')
def process_scanner_search_recipe():
    return jsonify(find_recipes_by_name(request.args.get('q', '')))


def create_new_session(uid, sesId, sesType):
    if uid not in active_brew_sessions:
        active_brew_sessions[uid] = PicoBrewSession()
    if sesType == 0:
        active_brew_sessions[uid].name = get_recipe_name_by_id(sesId)
    elif sesType in PICO_SESSION:
        active_brew_sessions[uid].name = PICO_SESSION[sesType]
    else:
        active_brew_sessions[uid].name = 'Unknown Session ({})'.format(sesType)

    # replace spaces and '#' with other character sequences
    encoded_recipe = active_brew_sessions[uid].name.replace(' ', '_').replace("#", "%23")
    filename = '{0}#{1}#{2}#{3}.json'.format(datetime.now().strftime('%Y%m%d_%H%M%S'), uid, sesId, encoded_recipe)
    active_brew_sessions[uid].filepath = brew_active_sessions_path().joinpath(filename)
    active_brew_sessions[uid].file = open(active_brew_sessions[uid].filepath, 'w')
    active_brew_sessions[uid].file.write('[')


def cleanup_old_session(uid):
    if uid in active_brew_sessions and active_brew_sessions[uid].file:
        active_brew_sessions[uid].file.seek(0, os.SEEK_END)
        active_brew_sessions[uid].file.seek(active_brew_sessions[uid].file.tell() - 1, os.SEEK_SET)  # Remove trailing , from last data set
        active_brew_sessions[uid].file.write('\n]\n')
        active_brew_sessions[uid].cleanup()
