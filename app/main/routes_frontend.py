import json
import math
import os
import uuid
from datetime import timedelta, datetime
from markupsafe import escape
from flask import current_app, make_response, request, send_file, render_template, redirect, url_for, jsonify
from pathlib import Path
from ruamel.yaml import YAML
from webargs import fields
from webargs.flaskparser import use_args
from werkzeug.utils import secure_filename


from . import main
from .config import (MachineType, SessionType, recipe_path,
                     brew_archive_sessions_path, ferm_archive_sessions_path, still_archive_sessions_path, iSpindel_archive_sessions_path, tilt_archive_sessions_path)
from .frontend_common import render_template_with_defaults
from .recipe_import import import_recipes
from .recipe_conditions import condition_charts
from .recipe_parser import PicoBrewRecipe, ZymaticRecipe, ZSeriesRecipe, ReduxRecipe
from .session_parser import (_paginate_sessions, list_session_files,
                             load_ferm_session, load_still_session, load_iSpindel_session, load_tilt_session,
                             dirty_sessions_since_clean, last_session_metadata, BrewSessionType,
                             get_brew_graph_data, get_ferm_graph_data, get_still_graph_data, get_iSpindel_graph_data, get_tilt_graph_data,
                             active_brew_sessions, active_ferm_sessions, active_still_sessions, active_iSpindel_sessions, active_tilt_sessions,
                             add_invalid_session, get_invalid_sessions, load_brew_sessions)
from .model import PICO_LOCATION, ZYMATIC_LOCATION, ZSERIES_LOCATION, SRM_COLOR_DATA, LOVIBOND_COLOR_DATA, bjcp_2008_substyles, MACHINE_BATCH_PRESETS, MACHINE_FIXED_WATER_GAL, FERMENTATION_TYPES

file_glob_pattern = "[!._]*.json"
yaml = YAML()


# -------- Routes --------
@main.route('/')
def index():
    return render_template_with_defaults('index.html', brew_sessions=load_active_brew_sessions(),
                           ferm_sessions=load_active_ferm_sessions(),
                           still_sessions=load_active_still_sessions(),
                           tilt_sessions=load_active_tilt_sessions(),
                           iSpindel_sessions=load_active_iSpindel_sessions())


def _paginated_sessions(stype, uid=None, offset=0, limit=None):
    sessions = []
    if stype == SessionType.BREW:
        sessions = load_brew_sessions(uid, offset, limit)
    elif stype == SessionType.PICOFERM:
        sessions = load_ferm_sessions(uid, offset, limit)
    elif stype == SessionType.PICOSTILL:
        sessions = load_still_sessions(uid, offset, limit)
    elif stype == SessionType.ISPINDEL:
        sessions = load_iSpindel_sessions(uid, offset, limit)
    elif stype == SessionType.TILT:
        sessions = load_tilt_sessions(uid, offset, limit)

    return sessions


def _session_type_history(stype):
    offset = request.args.get('offset', 0, type=int)
    limit = request.args.get('limit', 10, type=int)

    sessions = []
    try:
        sessions = _paginated_sessions(stype, None, offset, limit)
    except Exception as e:
        current_app.logger.error(f'failed to load brew sessions: {e}')
        if is_ajax(request):  # return error to loader, else return empty session list
            return f'unable to load more {stype.value} sessions', 404

    if is_ajax(request):
        return render_template('_session_list.html', session_type=stype, sessions=sessions)

    return render_template_with_defaults('session_history.html', session_type=stype, sessions=sessions, invalid=get_invalid_sessions(stype.value))


@main.route('/brew_history')
def brew_history():
    return _session_type_history(SessionType.BREW)


@main.route('/ferm_history')
def ferm_history():
    return _session_type_history(SessionType.PICOFERM)


@main.route('/still_history')
def still_history():
    return _session_type_history(SessionType.PICOSTILL)


@main.route('/iSpindel_history')
def iSpindel_history():
    return _session_type_history(SessionType.ISPINDEL)


@main.route('/tilt_history')
def tilt_history():
    return _session_type_history(SessionType.TILT)


@main.route('/zymatic_recipes')
def _zymatic_recipes():
    global zymatic_recipes, invalid_recipes
    zymatic_recipes = load_zymatic_recipes()
    recipes_dict = [json.loads(json.dumps(recipe, default=lambda r: r.__dict__)) for recipe in zymatic_recipes]
    return render_template_with_defaults('zymatic_recipes.html', recipes=recipes_dict, invalid=invalid_recipes.get(MachineType.ZYMATIC, set()))


@main.route('/new_zymatic_recipe', methods=['GET', 'POST'])
def new_zymatic_recipe():
    if request.method == 'POST':
        recipe = request.get_json()
        recipe['id'] = uuid.uuid4().hex[:32]
        filename = build_recipe_filename(recipe_path(MachineType.ZYMATIC), recipe['name'])
        return write_recipe_file(filename, recipe)
    else:
        return render_template_with_defaults('new_zymatic_recipe.html')


@main.route('/import_zymatic_recipe', methods=['GET', 'POST'])
def import_zymatic_recipe():
    if request.method == 'POST':
        data = request.get_json()
        guid = data['guid']  # user accountId
        uid = data['uid']  # machine productId
        try:
            # import for picobrew and picobrew_c are the same
            import_recipes(uid, guid, None, MachineType.ZYMATIC)
            return '', 204
        except Exception as e:
            current_app.logger.error(f'import of recipes failed: {e}')
            return getattr(e, 'message', e.args[0]), 400
    else:
        machine_ids = [uid for uid in active_brew_sessions if active_brew_sessions[uid].machine_type == MachineType.ZYMATIC]
        return render_template_with_defaults('import_brewhouse_recipe.html', user_required=True, machine_ids=machine_ids,
                                              post_url='/import_zymatic_recipe', redirect_url='/legacy_recipes', recipe_type='zymatic')


@main.route('/update_zymatic_recipe', methods=['POST'])
def update_zymatic_recipe():
    update_recipe = request.get_json()
    synced_files = list(recipe_path(MachineType.ZYMATIC).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.ZYMATIC, True).glob(file_glob_pattern))
    files = synced_files + archived_files

    for filename in files:
        recipe = load_zymatic_recipe(filename)
        if recipe.id == update_recipe['id']:
            recipe.update_recipe(filename, update_recipe)
    return '', 204


@main.route('/delete_zymatic_recipe', methods=['POST'])
def delete_zymatic_recipe():
    recipe_id = request.get_json()
    synced_files = list(recipe_path(MachineType.ZYMATIC).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.ZYMATIC, True).glob(file_glob_pattern))
    files = synced_files + archived_files

    for filename in files:
        recipe = load_zymatic_recipe(filename)
        if recipe.id == recipe_id:
            os.remove(filename)
            return '', 204
    return 'Delete Recipe: Failed to find recipe id \"' + recipe_id + '\"', 418


def load_zymatic_recipes(include_archived=True):
    synced_files = list(recipe_path(MachineType.ZYMATIC).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.ZYMATIC, True).glob(file_glob_pattern))

    files = synced_files
    if include_archived:
        files += archived_files

    current_app.logger.info(f'load_zymatic_recipes : {len(synced_files)} synced ; {len(archived_files)} archived ')
    recipes = [load_zymatic_recipe(file) for file in files]
    return list(sorted(filter(lambda x: x.name != None, recipes), key=lambda x: x.name))


def load_zymatic_recipe(file):
    recipe = ZymaticRecipe()
    parse_recipe(MachineType.ZYMATIC, recipe, file)

    recipe.name_escaped = escape(recipe.name).replace(" ", "_")
    return recipe


def get_zymatic_recipes(include_archived=True):
    global zymatic_recipes
    global zymatic_active_recipes
    return zymatic_recipes if include_archived else zymatic_active_recipes


@main.route('/zseries_recipes')
def _zseries_recipes():
    global zseries_recipes, invalid_recipes
    zseries_recipes = load_zseries_recipes()
    recipes_dict = [json.loads(json.dumps(recipe, default=lambda r: r.__dict__)) for recipe in zseries_recipes]
    return render_template_with_defaults('zseries_recipes.html', recipes=recipes_dict, invalid=invalid_recipes.get(MachineType.ZSERIES, set()))


@main.route('/new_zseries_recipe')
def new_zseries_recipe():
    return render_template_with_defaults('new_zseries_recipe.html')


@main.route('/new_zseries_recipe_save', methods=['POST'])
def new_zseries_recipe_save():
    recipe = request.get_json()
    recipe['id'] = increment_zseries_recipe_id()
    recipe['start_water'] = recipe.get('start_water', 13.1)
    filename = build_recipe_filename(recipe_path(MachineType.ZSERIES), recipe['name'])
    return write_recipe_file(filename, recipe)


@main.route('/update_zseries_recipe', methods=['POST'])
def update_zseries_recipe():
    update_recipe = request.get_json()
    synced_files = list(recipe_path(MachineType.ZSERIES).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.ZSERIES, True).glob(file_glob_pattern))
    files = synced_files + archived_files
    for filename in files:
        recipe = load_zseries_recipe(filename)
        if str(recipe.id) == update_recipe['id']:
            recipe.update_recipe(filename, update_recipe)
    return '', 204


@main.route('/device/<uid>/sessions/<session_type>', methods=['PUT'])
def update_device_session(uid, session_type):
    update = request.get_json()
    valid_session = True
    if session_type == 'ferm':
        session = active_ferm_sessions[uid]
    elif session_type == 'iSpindel':
        session = active_iSpindel_sessions[uid]
    elif session_type == 'tilt':
        session = active_tilt_sessions[uid]
    elif session_type == 'still':
        session = active_still_sessions[uid]
    else:
        valid_session = False

    if valid_session:
        if update['active'] == False:
            session.active = False
            if session.file != None:
                session.file.seek(0, os.SEEK_END)
                if session.file.tell() > 0:
                    # mark for completion and archive session file
                    session.file.seek(session.file.tell() - 1, os.SEEK_SET)  # Remove trailing comma from last data set
                    session.file.write('\n]')
                    session.cleanup()
                else:
                    # delete empty session file (user started fermentation, but device never reported data)
                    os.remove(session.filepath)
        else:
            session.active = True

            if session_type == 'still':
                try:
                    session.start_still_polling()
                except Exception as e:
                    current_app.logger.error(f'exception occured : {e}')
                    return getattr(e, 'message', e.args[0]), 418

        return '', 204
    else:
        current_app.logger.error(f'invalid session type : {session_type}')
        return f'Invalid session type provided {session_type}', 418


ALLOWED_EXTENSIONS = {'json'}


def allowed_extension(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def recipe_dirpath(machine_type):
    dirpath = None
    if machine_type == "picobrew" or machine_type == "pico":
        dirpath = recipe_path(MachineType.PICOBREW)
    elif machine_type == "zymatic":
        dirpath = recipe_path(MachineType.ZYMATIC)
    elif machine_type == "zseries":
        dirpath = recipe_path(MachineType.ZSERIES)
    return dirpath


@main.route('/recipes/<machine_type>', methods=['POST'])
def upload_file(machine_type):
    # check if the post request has the file part
    if 'recipe' not in request.files:
        current_app.logger.error('invalid input : no file part')
        return 'No file part', 400
    file = request.files['recipe']
    # if user does not select file, browser also
    # submit an empty part without filename
    if file.filename == '':
        current_app.logger.error('invalid input : no selected file')
        return 'no selected file', 400
    if file and allowed_extension(file.filename):
        filename = secure_filename(file.filename).replace(' ', '_')
        dirpath = recipe_dirpath(machine_type)
        if dirpath == None:
            current_app.logger.error(f'invalid input : unsupported machine_type {machine_type}')
            return 'unsupported machine_type', 400
        file.save(os.path.join(dirpath, filename))
        if machine_type in ('picobrew', 'pico'):
            refresh_pico_recipe_cache()
        return f'upload of {file.filename} successful', 204
    else:
        return f'unsupported file : {file.filename}', 400


@main.route('/recipes/<machine_type>/<id>/<name>.json', methods=['GET'])
def download_recipe(machine_type, id, name):
    dirpath = recipe_dirpath(machine_type)
    if dirpath == None:
        current_app.logger.error(f'invalid machine_type : {machine_type}')
        return f'Invalid machine type provided "{machine_type}"', 418

    files = list(dirpath.glob(file_glob_pattern))

    for filename in files:
        recipe = None
        if machine_type == "picobrew":
            recipe = load_pico_recipe(filename)
        elif machine_type == "zymatic":
            recipe = load_zymatic_recipe(filename)
        elif machine_type == "zseries":
            recipe = load_zseries_recipe(filename)

        # just to be sure check recipe.id and recipe.name replacing spaces with underscores (expected file naming by server)
        if recipe and str(recipe.id) == str(id) and str(recipe.name).replace(" ", "_") == name:
            response = make_response(send_file(filename))
            # custom content-type will force a download vs rendering with window.location
            response.headers['Content-Type'] = 'application/octet-stream'
            return response

    return f'Download Recipe: Failed to find recipe id "{id}" with name "{name}"', 418


@main.route('/sessions/<session_type>/<filename>', methods=['GET'])
def download_session(session_type, filename):
    session_dirpath = ""
    if session_type == "brew":
        session_dirpath = brew_archive_sessions_path()
    elif session_type == "ferm":
        session_dirpath = ferm_archive_sessions_path()
    elif session_type == "iSpindel":
        session_dirpath = iSpindel_archive_sessions_path()
    elif session_type == "tilt":
        session_dirpath = tilt_archive_sessions_path()
    elif session_type == "still":
        session_dirpath = still_archive_sessions_path()
    else:
        return f'Invalid session type provided "{session_type}"', 418

    files = list(session_dirpath.glob(file_glob_pattern))
    filepath = session_dirpath.joinpath(filename)

    for f in files:
        if f.name == filename:
            response = make_response(send_file(filepath))
            # custom content-type will force a download vs rendering with window.location
            response.headers['Content-Type'] = 'application/octet-stream'
            return response
    return f'Download Session: Failed to find session with filename "{filename}"', 418


@main.route('/delete_zseries_recipe', methods=['POST'])
def delete_zseries_recipe():
    recipe_id = request.get_json()
    synced_files = list(recipe_path(MachineType.ZSERIES).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.ZSERIES, True).glob(file_glob_pattern))
    files = synced_files + archived_files
    for filename in files:
        recipe = load_zseries_recipe(filename)
        if str(recipe.id) == recipe_id:
            os.remove(filename)
            return '', 204
    return f'Delete Recipe: Failed to find recipe id "{recipe_id}"', 418


@main.route('/import_zseries_recipe', methods=['GET', 'POST'])
def import_zseries_recipe():
    if request.method == 'POST':
        data = request.get_json()
        uid = data['uid']  # machine productId
        try:
            import_recipes(uid, None, None, MachineType.ZSERIES)
            return '', 204
        except Exception as e:
            current_app.logger.error(f'import of recipes failed: {e}')
            return getattr(e, 'message', e.args[0]), 400
    else:
        machine_ids = [uid for uid in active_brew_sessions if active_brew_sessions[uid].machine_type == MachineType.ZSERIES]
        return render_template_with_defaults('import_brewhouse_recipe.html', user_required=False, machine_ids=machine_ids,
                                              post_url='/import_zseries_recipe', redirect_url='/legacy_recipes', recipe_type='zseries')


def load_zseries_recipes(include_archived=True):
    synced_files = list(recipe_path(MachineType.ZSERIES).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.ZSERIES, True).glob(file_glob_pattern))

    files = synced_files
    if include_archived:
        files += archived_files

    current_app.logger.info(f'load_zseries_recipes : {len(synced_files)} synced ; {len(archived_files)} archived ')
    recipes = [load_zseries_recipe(file) for file in files]
    return list(sorted(filter(lambda x: x.name != None, recipes), key=lambda x: x.name))


def load_zseries_recipe(file):
    recipe = ZSeriesRecipe()
    parse_recipe(MachineType.ZSERIES, recipe, file)
    recipe.name_escaped = escape(recipe.name).replace(" ", "_")
    return recipe


def load_ingredients():
    filepath = current_app.config['RECIPES_PATH']
    filepath = filepath.joinpath('ingredients/ingredients.json')
    try:
        ingredients = None
        with open(filepath) as f:
            ingredients = json.load(f)
            return ingredients
    except Exception as e:
        current_app.logger.error("ERROR: An exception occurred parsing ingredients {}".format(filepath))
        current_app.logger.error(e)

def parse_recipe(machineType, recipe, file):
    try:
        recipe.parse(file)
    except Exception as e:
        current_app.logger.error("ERROR: An exception occurred parsing recipe {}".format(file))
        current_app.logger.error(e)
        add_invalid_recipe(machineType, file)


def get_zseries_recipes(include_archived=True):
    global zseries_recipes
    global zseries_active_recipes
    return zseries_recipes if include_archived else zseries_active_recipes


def get_invalid_recipes():
    global invalid_recipes
    return invalid_recipes


def add_invalid_recipe(deviceType, file):
    global invalid_recipes
    if deviceType not in invalid_recipes:
        invalid_recipes[deviceType] = set()
    invalid_recipes.get(deviceType).add(file)


@main.route('/delete_file', methods=['POST'])
def delete_file():
    body = request.get_json()
    filename = body['filename']
    if body['type'] == "recipe":
        filepath = Path(filename)
        if filepath:
            os.remove(filename)
            for device in invalid_recipes:
                if filepath in invalid_recipes[device]:
                    invalid_recipes[device].remove(Path(filename))
            return '', 204
        current_app.logger.error("ERROR: failed to delete recipe file {}".format(filename))
        return "Delete Filename: Failed to find recipe file {}".format(filename), 418
    elif body['type'] in ['brew', 'ferm', 'iSpindel', 'tilt', 'still']:
        filepath = Path(filename)
        if filepath:
            os.remove(filename)
            if body['type'] in invalid_sessions and filepath in invalid_sessions[body['type']]:
                invalid_sessions[body['type']].remove(Path(filename))
            return '', 204
        current_app.logger.error("ERROR: failed to delete {} session file {}".format(body['type'], filename))
        return "Delete Filename: Failed to find {} session file {}".format(body['type'], filename), 418
    else:
        current_app.logger.error("ERROR: failed to delete {} as the file type {} was not supported".format(filename, body['type']))
    return 'Delete Filename: Unsupported file type specified {}'.format(body['type']), 418



def format_datetime_filter(value, format="%m/%d/%Y"):
    try:
        datetime_object = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f")
    except Exception:
        datetime_object = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    value = datetime_object.strftime(format)
    return value
main.add_app_template_filter(format_datetime_filter, 'format_datetime')


def srm_color_filter(value):
    """SRM_COLOR_DATA (model.py) only has entries for 1-30 plus every 5th value up to 60 --
    a direct dict[key] lookup (recipe_list_redux.html) throws for anything else, which
    real-world recipes hit constantly (very dark/high-SRM styles, or an SRM outside 1-60
    entirely). Falls back to the nearest defined key instead of erroring the whole page."""
    try:
        key = int(round(float(value)))
    except (TypeError, ValueError):
        key = 1
    if key in SRM_COLOR_DATA:
        return SRM_COLOR_DATA[key]
    keys = sorted(SRM_COLOR_DATA.keys())
    nearest = min(keys, key=lambda k: abs(k - key))
    return SRM_COLOR_DATA[nearest]
main.add_app_template_filter(srm_color_filter, 'srm_color')

@main.route('/recipes')
def _recipes():
    global redux_recipes, invalid_recipes
    redux_recipes = load_redux_recipes()
    for r in redux_recipes:
        for s in brew_sessions:
            print(s['alias'])
            #TODO: Get recipe brew count and last brew date
    recipes_dict = [json.loads(json.dumps(recipe, default=lambda r: r.__dict__)) for recipe in redux_recipes]
    for d, recipe in zip(recipes_dict, redux_recipes):
        d['machine_problems'] = recipe.machine_problems()
    return render_template_with_defaults('redux_recipes.html', recipes=recipes_dict, invalid=invalid_recipes.get(MachineType.ZSERIES, set()), SRM_COLOR_DATA=SRM_COLOR_DATA)

#   Recipe: /API/pico/getRecipe?rfid={rfid}
# Response: HTML
get_recipe_args = {
    'rfid': fields.Str(required=True)      # 14 character alpha-numeric PicoPak RFID
}

@main.route('/recipe')
@use_args(get_recipe_args, location='querystring')
def _recipe(args):
    global redux_recipes, invalid_recipes
    redux_recipes = load_redux_recipes()
    recipe = new_zymatic_recipe()
    for r in redux_recipes:
        if r.id == args['rfid']:
            recipe = json.loads(json.dumps(r, default=lambda r: r.__dict__))
    GB_labels = [f['Name'] for f in recipe['Fermentables']]
    GB_amounts = [f['Amount'] for f in recipe['Fermentables']]
    GRAIN_BILL_DATA = {
        'labels': GB_labels, 'data': GB_amounts
    }
    HB_labels = [h['Name'] for h in recipe['Hops']]
    HB_amounts = [h['Amount'] for h in recipe['Hops']]
    HOPS_BILL_DATA = {
        'labels': HB_labels, 'data': HB_amounts
    }
    wortCurveData = []
    for s in recipe['MachineSteps']:
        for m in range(s['Time']):
            wortCurveData.append(int(s['Temperature']))
    derived = recipe_derived_values(recipe)
    derived['condition_data'] = condition_charts(recipe)
    fill_default_instructions(recipe)
    for s in range(len(recipe['Hops'])):
        for k in ZSERIES_LOCATION.keys():
            if ZSERIES_LOCATION[k] == str(recipe['Hops'][s]['Location']):
                recipe['Hops'][s]['Location'] = k.replace("Adjunct", "Adjunct ")
    for s in range(len(recipe['BoilSteps'])):
        for k in ZSERIES_LOCATION.keys():
            if ZSERIES_LOCATION[k] == str(recipe['BoilSteps'][s]['Location']):
                recipe['BoilSteps'][s]['Location'] = k.replace("Adjunct", "Adjunct ")
    for s in range(len(recipe['WhirlpoolSteps'])):
        for k in ZSERIES_LOCATION.keys():
            if ZSERIES_LOCATION[k] == str(recipe['WhirlpoolSteps'][s]['Location']):
                recipe['WhirlpoolSteps'][s]['Location'] = k.replace("Adjunct", "Adjunct ")
    for s in range(len(recipe['MachineSteps'])):
        for k in ZSERIES_LOCATION.keys():
            if ZSERIES_LOCATION[k] == str(recipe['MachineSteps'][s]['StepLocation']):
                recipe['MachineSteps'][s]['StepLocation'] = k.replace("Adjunct", "Adjunct ").replace("PassThru", "Pass Through")
    return render_template_with_defaults('recipe_viewer.html', recipe=recipe, grain_data=GRAIN_BILL_DATA, hops_data=HOPS_BILL_DATA, wortCurveData=wortCurveData, fermentation_types=FERMENTATION_TYPES, **derived)

# Constants and algorithm ported exactly from picobrew.com's own Crafter JS
# (getBrewGraphData/minutesToTimeString) so the estimate matches the live site bit-for-bit,
# not just approximately -- verified against a real recipe's printed page (3 hrs 0 min /
# 2 hrs 15 mins reproduced exactly for its MachineSteps).
HEATING_SLOPE_C_PER_MIN = 4.96
HEAT_LOSS_SLOPE_C_PER_MIN = 0.585
HEATING_SLOPE_F_PER_MIN = HEATING_SLOPE_C_PER_MIN * 9 / 5
HEAT_LOSS_SLOPE_F_PER_MIN = HEAT_LOSS_SLOPE_C_PER_MIN * 9 / 5


def minutes_to_time_string(n):
    hours = n / 60
    rhours = math.floor(hours)
    rminutes = round((hours - rhours) * 60)
    return f"{rhours} hr{'s' if rhours > 1 else ''} and {rminutes} min{'s' if rminutes > 1 else ''}"


def estimate_recipe_brew_chill_time(recipe):
    steps = recipe.get('MachineSteps') or []
    if not steps:
        return minutes_to_time_string(0), minutes_to_time_string(0)

    is_metric = recipe.get('UseMetric', False)
    heating_slope = HEATING_SLOPE_C_PER_MIN if is_metric else HEATING_SLOPE_F_PER_MIN
    heat_loss_slope = HEAT_LOSS_SLOPE_C_PER_MIN if is_metric else HEAT_LOSS_SLOPE_F_PER_MIN
    current_time = 0.0
    current_temp = 12.7 if is_metric else 55
    chill_time = 0.0
    last_index = len(steps) - 1

    for i, step in enumerate(steps):
        is_chill_step = (i == last_index) and step['Temperature'] < current_temp
        if step['StepLocation'] == 6:  # Pause -- no time or temperature change
            continue
        next_temp = step['Temperature']
        if next_temp <= 0:
            continue
        if next_temp != current_temp:
            change_temp_time = ((next_temp - current_temp) / heating_slope if next_temp > current_temp
                                 else (current_temp - next_temp) / heat_loss_slope)
            if is_chill_step:
                chill_time = change_temp_time
            else:
                current_time += change_temp_time
        if not is_chill_step:
            current_temp = next_temp
            current_time += step['Time']
            if step.get('Drain', 0) > 0:
                current_time += step['Drain']

    return minutes_to_time_string(round(current_time)), minutes_to_time_string(round(chill_time))


def recipe_derived_values(recipe):
    """Display-only values computed from a recipe, shared by the viewer and editor pages.
    Expects raw (numeric) MachineSteps StepLocation codes -- call before any display mapping.

    pre_hop_boil_time matches picobrew's own Crafter JS exactly (getMaxBoilStepTime(): the boil
    runs BoilTime minutes total, and this is how long it ran before its longest-boiled hop or
    boil adjunct went in). whirlpool_time mirrors that same "longest thing added" shape for the
    whirlpool stage (no reference implementation available for this one)."""
    max_boil_step_time = max([h['Time'] for h in recipe['Hops']] +
                             [a['Time'] for a in recipe['Adjuncts']], default=0)
    whirlpool_time = max([s['Time'] for s in recipe['WhirlpoolSteps']] +
                         [h['Time'] for h in recipe['WhirlpoolHops']] +
                         [a['Time'] for a in recipe['WhirlpoolAdjuncts']], default=0)
    brew_time, chill_time = estimate_recipe_brew_chill_time(recipe)
    return {
        'pre_hop_boil_time': recipe['BoilTime'] - max_boil_step_time,
        'whirlpool_time': whirlpool_time,
        'brew_time': brew_time,
        'chill_time': chill_time,
    }


def fill_default_instructions(recipe):
    if not recipe.get('BrewingInstructionsText'):
        recipe['BrewingInstructionsText'] = build_default_brewing_instructions(recipe)
    if not recipe.get('FermentationInstructionsText'):
        recipe['FermentationInstructionsText'] = build_default_fermentation_instructions(recipe)


def build_default_brewing_instructions(recipe):
    human = recipe.get('HumanBrewingSteps') or {}
    lines = []
    if human.get('FillKeg'):
        lines.append(human['FillKeg'])
    if human.get('LoadMash'):
        lines.append(human['LoadMash'])
        for m in human.get('Mash', []):
            lines.append('- {} {} of {}'.format(m['Amount'], m['Units'], m['Name']))
    for round_ in human.get('AdjunctRounds', []):
        if human.get('LoadAdjuncts'):
            lines.append(human['LoadAdjuncts'])
        for n in range(1, 5):
            adjuncts = round_.get(f'Adjuncts{n}', [])
            if adjuncts:
                lines.append(f'- Load the following ingredients in Adjunct {n}')
                for i in adjuncts:
                    lines.append('  - {} {} of {}'.format(i['Amount'], i['Units'], i['Name']))
    return '\n'.join(lines)


def build_default_fermentation_instructions(recipe):
    steps = recipe.get('FermentationSteps') or []
    human = recipe.get('HumanBrewingSteps') or {}
    yeast = recipe.get('Yeast') or {}
    is_metric = recipe.get('UseMetric', False)
    fermentation_type = recipe.get('FermentationType', 0)
    lines = []

    # Matches picobrew's own Crafter JS exactly: "Cool to X" is the yeast's expected pitch
    # temperature (falling back to 68°F / 20°C if unset), *not* the fermentation step's own
    # temp -- those can legitimately differ (e.g. a later crash-chill step).
    expected_temp = yeast.get('ExpectedTemp')
    cool_to_temp = expected_temp if expected_temp and expected_temp > 0 else (20 if is_metric else 68)
    lines.append('Cool to {:g}{}'.format(cool_to_temp, '°C' if is_metric else '°F'))

    lines.append('Pitch Yeast')
    if human.get('LoadFermentationAdditions'):
        lines.append(human['LoadFermentationAdditions'])
        for fa in human.get('FermentationAdditions', []):
            lines.append('- {} {} of {}'.format(fa['Amount'], fa['Units'], fa['Name']))

    # FermentationType: 0=Ale, 1=Lager, 2=Advanced/Custom -- matches picobrew's own template
    # exactly (ng-if="recipe.FermentationType != 2"): Ale shows the actual configured days,
    # Lager shows a fixed reference lagering schedule regardless of FermentationSteps, and
    # Advanced/Custom schedules get no generic line at all.
    if fermentation_type == 1:
        lagering_temp = '1.6°C' if is_metric else '35°F'
        lines.append('Keep temperature consistent for 3 weeks. Rack to a lagering container '
                      'and keep at {} for another 4 weeks'.format(lagering_temp))
    elif fermentation_type != 2 and steps:
        lines.append('Keep temperature consistent for {:.1f} Days'.format(steps[0]['Days']))

    return '\n'.join(lines)


def build_style_suggestions():
    """Item 6: mines the user's own local recipe library for which yeasts/hops are actually
    paired with each BJCP style, used to suggest starting points on the recipe editor and
    the new-recipe-from-style wizard. Never fabricates a suggestion -- a style with no local
    recipes returns empty lists rather than a generic guess."""
    from collections import Counter
    by_style = {}
    for r in load_redux_recipes():
        style = getattr(r, 'StyleNameCode', None)
        if not style:
            continue
        entry = by_style.setdefault(style, {'yeasts': Counter(), 'hops': Counter()})
        yeast_name = (getattr(r, 'Yeast', None) or {}).get('Name')
        if yeast_name:
            entry['yeasts'][yeast_name] += 1
        for h in getattr(r, 'Hops', None) or []:
            if h.get('Name'):
                entry['hops'][h['Name']] += 1
    return {
        style: {
            'yeasts': [name for name, _ in data['yeasts'].most_common(3)],
            'hops': [name for name, _ in data['hops'].most_common(3)],
        }
        for style, data in by_style.items()
    }


@main.route('/api/style_suggestions')
def _api_style_suggestions():
    style = request.args.get('style', '')
    suggestions = build_style_suggestions().get(style, {'yeasts': [], 'hops': []})
    return jsonify(suggestions)


def generate_recipe_from_style(style, batch_size, suggestions, machine='Custom'):
    """Item 2: builds a starting-point recipe targeting the midpoint of the given style's
    OG/IBU/SRM ranges. Reuses the exact calibrated formulas already validated for live
    recalculation elsewhere on the editor (DEFAULT_GRAVITY_CONSTANT/DEFAULT_COLOR_CONSTANT in
    recipe_ingredients.js, the Tinseth IBU calibration in recipe_calculations.js) rather than
    a separate, unverified formula set -- both were calibrated at a 2.5 gal baseline, so this
    always builds at that baseline first and then scales to the requested batch size using
    the same scaling identity Item 3 relies on: Amount/BatchSize/H2O scale together, while
    PotentialGravity/ColorPts/IBU (concentration values) do not change."""
    catalog = load_ingredient_database()
    BASELINE_BATCH = 2.5
    GRAVITY_K = 0.2068
    COLOR_K = 0.242
    TINSETH_CAL = 0.9074

    def tinseth_ibu(amount_oz, alpha_pct, time_min, og):
        bigness = 1.65 * 0.000125 ** (og - 1)
        boil_factor = (1 - math.exp(-0.04 * time_min)) / 4.15
        return (amount_oz * (alpha_pct / 100) * bigness * boil_factor * 7489 * TINSETH_CAL) / BASELINE_BATCH

    target_og = (style['MinOG'] + style['MaxOG']) / 2
    target_ibu = (style['MinIBU'] + style['MaxIBU']) / 2
    target_srm = (style['MinSRM'] + style['MaxSRM']) / 2
    target_og_pts = (target_og - 1) * 1000

    # Fermentables: one base malt sized for ~90% of target gravity, plus one specialty grain
    # (if the base malt's own color falls short of the style's target SRM) to close the color
    # gap -- ColorPts/PotentialGravity here are exact given the chosen Amount (same formula
    # the editor's own autofill uses), not estimates layered on top of an estimate.
    grains = [f for f in catalog['Fermentables'] if f['FermentableType'] == 'Grain' and f['Yield'] > 0]
    # Specialty grain first, picked dark enough that hitting the SRM target doesn't take an
    # implausible amount of weight (a fixed "~40L crystal" choice works for an amber ale but
    # blows the gravity budget wide open for a stout, which needs its color from a little
    # roasted barley/black patent, not a lot of crystal -- caught by item 1's own compliance
    # coloring going red on a first pass of this generator). Base malt is then sized to make
    # up exactly whatever gravity the specialty grain didn't already contribute, so the two
    # always sum to target_og_pts by construction rather than by a fixed 90/10 split.
    base_malt = min(grains, key=lambda f: f['Color'])
    fermentables = []
    specialty_gravity_pts = 0
    if target_srm > 3:
        target_specialty_color = min(600, max(15, target_srm * 15))
        specialty_pool = [f for f in grains if f['Color'] > base_malt['Color'] + 5]
        specialty = min(specialty_pool, key=lambda f: abs(f['Color'] - target_specialty_color)) if specialty_pool else None
        if specialty:
            specialty_amount = round(max(target_srm / (specialty['Color'] * COLOR_K), 0.05), 2)
            specialty_gravity_pts = specialty_amount * specialty['Yield'] * GRAVITY_K
            fermentables.append({
                'Amount': specialty_amount, 'ColorPts': round(specialty_amount * specialty['Color'] * COLOR_K, 2),
                'Errors': [], 'PotentialGravity': round(specialty_gravity_pts, 2),
                'FermentableID': specialty.get('FermentableID', 0), 'Name': specialty['Name'],
                'Color': specialty['Color'], 'Yield': specialty['Yield'], 'FermentableType': specialty['FermentableType'],
            })

    base_og_pts = max(target_og_pts - specialty_gravity_pts, target_og_pts * 0.5)
    base_amount = round(base_og_pts / (base_malt['Yield'] * GRAVITY_K), 2)
    fermentables.insert(0, {
        'Amount': base_amount, 'ColorPts': round(base_amount * base_malt['Color'] * COLOR_K, 2), 'Errors': [],
        'PotentialGravity': round(base_og_pts, 2), 'FermentableID': base_malt.get('FermentableID', 0),
        'Name': base_malt['Name'], 'Color': base_malt['Color'], 'Yield': base_malt['Yield'],
        'FermentableType': base_malt['FermentableType'],
    })

    # Hops: prefer whatever the user's own recipe library actually pairs with this style
    # (item 6's suggestions); fall back to a general-purpose ~6% AA hop when there's no
    # local data for it. One 60-min bittering addition (75% of target IBU) plus one 15-min
    # flavor addition (25%).
    hop_matches = [h for h in (next((c for c in catalog['Hops'] if c['Name'] == n), None) for n in suggestions.get('hops', [])) if h]
    if not hop_matches:
        hop_matches = sorted((h for h in catalog['Hops'] if h['Alpha'] > 0), key=lambda h: abs(h['Alpha'] - 6))[:2]
    if len(hop_matches) == 1:
        hop_matches = hop_matches * 2

    def size_hop_for_ibu(alpha_pct, time_min, ibu_target):
        bigness = 1.65 * 0.000125 ** (target_og - 1)
        boil_factor = (1 - math.exp(-0.04 * time_min)) / 4.15
        denom = (alpha_pct / 100) * bigness * boil_factor * 7489 * TINSETH_CAL / BASELINE_BATCH
        return round(max(ibu_target / denom, 0.1), 2) if denom > 0 else 0.5

    bittering, flavor = hop_matches[0], hop_matches[1]
    bittering_amount = size_hop_for_ibu(bittering['Alpha'], 60, target_ibu * 0.75)
    flavor_amount = size_hop_for_ibu(flavor['Alpha'], 15, target_ibu * 0.25)
    hops = [
        {'Location': 2, 'Amount': bittering_amount, 'IBU': round(tinseth_ibu(bittering_amount, bittering['Alpha'], 60, target_og), 2),
         'Time': 60.0, 'BoilUse': 0, 'Errors': [], 'AdjunctRound': 1, 'HopID': bittering.get('HopID', 0),
         'Name': bittering['Name'], 'Alpha': bittering['Alpha']},
        {'Location': 3, 'Amount': flavor_amount, 'IBU': round(tinseth_ibu(flavor_amount, flavor['Alpha'], 15, target_og), 2),
         'Time': 15.0, 'BoilUse': 0, 'Errors': [], 'AdjunctRound': 1, 'HopID': flavor.get('HopID', 0),
         'Name': flavor['Name'], 'Alpha': flavor['Alpha']},
    ]

    # Yeast: same "trust the user's own local pairing data first" preference as hops.
    yeast_match = next((y for y in (next((c for c in catalog['Yeast'] if c['Name'] == n), None)
                                     for n in suggestions.get('yeasts', [])) if y), None)
    yeast = yeast_match or min((y for y in catalog['Yeast'] if y['ExpectedAtten'] > 0), key=lambda y: abs(y['ExpectedAtten'] - 75))
    fermentation_type = 1 if (yeast['MinTemp'] + yeast['MaxTemp']) / 2 < 58 else 0
    fg = 1 + (target_og - 1) * (1 - yeast['ExpectedAtten'] / 100)
    abv = round((target_og - fg) * 131.25, 2)

    # Scale from the calibration baseline to the requested batch size (Item 3's identity:
    # only weights/volumes scale, the already-correct points fields above do not).
    scale_ratio = batch_size / BASELINE_BATCH
    for f in fermentables:
        f['Amount'] = round(f['Amount'] * scale_ratio, 2)
    for h in hops:
        h['Amount'] = round(h['Amount'] * scale_ratio, 2)
    h2o = MACHINE_FIXED_WATER_GAL.get(machine, round(BASELINE_BATCH * 1.43 * scale_ratio, 2))

    mash_steps = [{'Name': 'Single Step Infusion Mash', 'Temp': 152.0, 'Time': 60.0, 'Style': 'Infusion', 'Errors': []}]
    boil_steps = [
        {'Name': 'Adjunct 1', 'Temp': 207, 'Time': 60, 'BoilTime': 60, 'Ramp': True, 'IsWhirlpool': False, 'AdjunctRound': 1, 'Location': 2, 'Errors': []},
        {'Name': 'Adjunct 2', 'Temp': 207, 'Time': 15, 'BoilTime': 15, 'Ramp': False, 'IsWhirlpool': False, 'AdjunctRound': 1, 'Location': 3, 'Errors': []},
    ]
    fermentation_steps = [
        {'Step': 1, 'Name': 'Fermentation', 'Temp': yeast['ExpectedTemp'], 'Days': 10.0 if fermentation_type == 0 else 21.0, 'Hours': 0.0, 'Minutes': 0},
        {'Step': 2, 'Name': 'Crash Chill', 'Temp': 44.0, 'Days': 0.0, 'Hours': 0.0, 'Minutes': 0},
    ]
    machine_steps = [
        {'ID': 0, 'Step': 0, 'Name': 'Heat Water', 'Temperature': 152, 'Time': 0, 'Drain': 0, 'StepLocation': 0},
        {'ID': 0, 'Step': 1, 'Name': 'Mash', 'Temperature': 152, 'Time': 60, 'Drain': 8, 'StepLocation': 1},
        {'ID': 0, 'Step': 2, 'Name': 'Heat to Boil', 'Temperature': 207, 'Time': 0, 'Drain': 0, 'StepLocation': 0},
        {'ID': 0, 'Step': 3, 'Name': 'Boil Adjunct 1', 'Temperature': 207, 'Time': 45, 'Drain': 0, 'StepLocation': 2},
        {'ID': 0, 'Step': 4, 'Name': 'Boil Adjunct 2', 'Temperature': 207, 'Time': 15, 'Drain': 5, 'StepLocation': 3},
        {'ID': 0, 'Step': 5, 'Name': 'Connect Chiller', 'Temperature': 0, 'Time': 0, 'Drain': 0, 'StepLocation': 6},
        {'ID': 0, 'Step': 6, 'Name': 'Chill', 'Temperature': 65, 'Time': 10, 'Drain': 10, 'StepLocation': 0},
    ]
    water_stats = [
        {'Item1': 'Starting Water', 'Item2': f'{h2o} gal Water ({round(h2o * 8.34, 2)} lbs)'},
        {'Item1': 'Batch Size', 'Item2': f'{batch_size} Gal'},
    ]
    human_brewing_steps = {
        'FillKeg': f'Fill keg with {h2o} Gal water',
        'LoadMash': 'Mill grain and load into mash compartment',
        'Mash': [{'Name': f['Name'], 'Units': 'lbs', 'Amount': f['Amount']} for f in fermentables],
        'LoadAdjuncts': 'Load hops and other boil/whirlpool additions into adjunct compartments:',
        'AdjunctRounds': [{
            'Round': 1, 'AddInTime': 0,
            'Adjuncts1': [{'Name': hops[0]['Name'], 'Units': 'oz', 'Amount': hops[0]['Amount']}],
            'Adjuncts2': [{'Name': hops[1]['Name'], 'Units': 'oz', 'Amount': hops[1]['Amount']}],
            'Adjuncts3': [], 'Adjuncts4': [],
        }],
    }

    recipe_name = f"{style['StyleNameCode']} ({batch_size:g} gal)"
    beer_style = {k: v for k, v in style.items() if k not in ('ColorHEX', 'ColorCategory')}
    vm_recipe = {
        'RecipeID': 0, 'GUID': None, 'PreviousGUID': None, 'Efficiency': 55.0, 'Author': 'You',
        'Name': recipe_name, 'WortSize': None, 'BatchSize': round(batch_size, 2), 'CreationDate': datetime.now().isoformat(),
        'ABV': abv, 'IBU': round(target_ibu, 1), 'SRM': round(target_srm, 1), 'FG': round(fg, 4), 'OG': round(target_og, 4),
        'Version': 1, 'StyleID': style.get('StyleID', 0), 'BeerStyle': beer_style, 'MashProfile': None,
        'Notes': f"Generated starting point for {style['StyleNameCode']} -- review and adjust before brewing.",
        'SyncStatus': False, 'H2O': h2o, 'RecipeFile': None, 'Locked': False, 'Shared': False,
        'OriginalName': recipe_name, 'OriginalAuthor': 'You', 'TastingNotes': '', 'BoilSize': None,
        'Deleted': False, 'Imported': False, 'OriginalAuthorID': 0, 'WhirlpoolTemp': None,
        'BoilTemp': 207, 'BoilTime': 60, 'MashTime': 60, 'MashTemp': 152, 'MashType': 0,
        'IsFirstWort': False, 'Errors': [], 'Warnings': [],
        'Fermentables': fermentables, 'Hops': hops, 'DryHops': [], 'WhirlpoolHops': [],
        'Adjuncts': [], 'WhirlpoolAdjuncts': [], 'DryAdjuncts': [],
        'Yeast': dict(yeast), 'Amendments': [], 'MachineSteps': machine_steps,
        'FermentationSteps': fermentation_steps, 'FermentationType': fermentation_type,
        'BoilSteps': boil_steps, 'MashSteps': mash_steps, 'WhirlpoolSteps': [],
        'IsMetric': False, 'SessionCount': 0, 'CrafterVersion': 1, 'IsBiB': False, 'RecipeType': 0,
        'HumanBrewingSteps': human_brewing_steps, 'BrewingInstructionsText': None,
        'FermentationInstructionsText': None,
    }
    new_guid = uuid.uuid4().hex
    full_json = {
        'RecipeGUID': new_guid, 'UseMetric': False, 'Machine': machine,
        'VM': {
            'Recipe': vm_recipe,
            'Content': {'Sections': [{'Stats': water_stats}], 'SpecialBrewingInstructions': ''},
        },
    }
    safe_name = recipe_name.replace(' ', '_').replace("'", '').replace('/', '-')
    filename = recipe_path(MachineType.UNIFIED).joinpath(f'{safe_name}.json')
    with open(filename, 'w') as f:
        json.dump(full_json, f, indent=4, sort_keys=True)
    return new_guid


@main.route('/recipe/new_from_style', methods=['GET', 'POST'])
def _recipe_new_from_style():
    catalog = load_ingredient_database()
    if request.method == 'POST':
        style_name = request.form.get('style_name', '')
        style_guide = request.form.get('style_guide', '')
        try:
            batch_size = float(request.form.get('batch_size') or 2.5)
        except ValueError:
            batch_size = 2.5
        machine = request.form.get('machine') or 'Custom'
        style = next((s for s in catalog['BeerStyles']
                      if s['StyleNameCode'] == style_name and s['StyleGuide'] == style_guide), None)
        if not style:
            return 'Style not found', 404
        suggestions = build_style_suggestions().get(style_name, {'yeasts': [], 'hops': []})
        new_guid = generate_recipe_from_style(style, batch_size, suggestions, machine)
        return redirect(f'/recipe/edit/{new_guid}')

    styles_by_guide = {}
    for s in catalog['BeerStyles']:
        styles_by_guide.setdefault(s['StyleGuide'], []).append(s)
    for guide_styles in styles_by_guide.values():
        guide_styles.sort(key=lambda s: (int(s.get('CatNumCode') or 0), s.get('CatLettCode') or '', s['StyleNameCode']))
    return render_template_with_defaults('new_recipe_from_style.html', styles_by_guide=styles_by_guide, machine_presets=MACHINE_BATCH_PRESETS)


def machine_redux_recipes(include_hidden=False):
    """Active builder recipes that are safe to serve to a Zymatic/Z-Series. Menus pass
    include_hidden=False so "Hide on Machine" drops them; lookups by id/name include hidden
    ones so a session started before the recipe was hidden still resolves."""
    recipes = []
    for r in load_redux_recipes(False):
        problems = r.machine_problems()
        if problems:
            current_app.logger.debug(f'not serving recipe "{r.name}" to machines: {"; ".join(problems)}')
        elif include_hidden or not r.HideOnMachine:
            recipes.append(r)
    return recipes


@main.route('/recipe/<rfid>/hide_on_machine', methods=['POST'])
def _recipe_hide_on_machine(rfid):
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get('hidden'), bool):
        return jsonify({'error': '"hidden" must be true or false'}), 400
    recipe = next((r for r in load_redux_recipes() if r.id == rfid), None)
    if recipe is None:
        return jsonify({'error': 'Recipe not found'}), 404
    recipe.set_hide_on_machine(body['hidden'])
    return jsonify({'id': recipe.id, 'hidden': recipe.HideOnMachine})


@main.route('/recipe/edit/<rfid>', methods=['GET', 'POST'])
def _recipe_edit(rfid):
    global redux_recipes, invalid_recipes
    redux_recipes = load_redux_recipes()
    recipe_obj = next((r for r in redux_recipes if r.id == rfid), None)

    if request.method == 'POST':
        if recipe_obj is None:
            return 'Recipe not found', 404
        recipe_obj.update_from_form(request.form)
        return redirect(f'/recipe/edit/{recipe_obj.id}')

    recipe = json.loads(json.dumps(recipe_obj, default=lambda r: r.__dict__)) if recipe_obj else new_zymatic_recipe()
    GB_labels = [f['Name'] for f in recipe['Fermentables']]
    GB_amounts = [f['Amount'] for f in recipe['Fermentables']]
    GRAIN_BILL_DATA = {
        'labels': GB_labels, 'data': GB_amounts
    }
    HB_labels = [h['Name'] for h in recipe['Hops']]
    HB_amounts = [h['Amount'] for h in recipe['Hops']]
    HOPS_BILL_DATA = {
        'labels': HB_labels, 'data': HB_amounts
    }
    wortCurveData = []
    for s in recipe['MachineSteps']:
        for m in range(s['Time']):
            wortCurveData.append(int(s['Temperature']))
    # Boil/Whirlpool Steps and Machine Steps location is left as its raw ZSERIES_LOCATION
    # code (not converted to a display string) so the editable <select> in
    # recipe_editor.html can mark the right option selected.

    fill_default_instructions(recipe)

    location_options = [(v, k.replace('Adjunct', 'Adjunct ').replace('PassThru', 'Pass Through'))
                         for k, v in ZSERIES_LOCATION.items()]

    return render_template_with_defaults('recipe_editor.html', recipe=recipe, grain_data=GRAIN_BILL_DATA, hops_data=HOPS_BILL_DATA, wortCurveData=wortCurveData, bjcp_2008_substyles=bjcp_2008_substyles, location_options=location_options, machine_presets=MACHINE_BATCH_PRESETS, machine_fixed_water=MACHINE_FIXED_WATER_GAL, fermentation_types=FERMENTATION_TYPES, **recipe_derived_values(recipe))

@main.route('/recipe/clone/<rfid>', methods=['GET'])
def _recipe_clone(rfid):
    # if request.method == 'DELETE':
    newID = uuid.uuid4().hex[:32]
    print(rfid + " : " + request.method)
    print("New ID:" + newID)
    return '', 204

@main.route('/recipe/delete/<rfid>', methods=['GET'])
def _recipe_delete(rfid):
    # if request.method == 'DELETE':
    print(rfid + " : " + request.method)
    return '', 204


# Maps each ingredients-page category to the field that identifies an entry, used both to
# merge the picobrew catalog with the user's custom additions and to validate new-ingredient
# submissions below.
INGREDIENT_NAME_KEYS = {
    'Fermentables': 'Name',
    'Hops': 'Name',
    'Yeast': 'Name',
    'Adjuncts': 'Name',
    'WaterAmendments': 'Name',
    'BeerStyles': 'StyleNameCode',
}


def merge_ingredient_list(catalog_items, custom_items, name_key, guide_key=None):
    """Merges a picobrew catalog list with the user's custom entries, keyed by name -- a
    custom entry sharing a name with a catalog one overrides it (so a user can correct or
    override a catalog value by re-adding it), rather than being silently hidden.

    guide_key exists for BeerStyles specifically: the same style name legitimately has a
    different entry per BJCP guide year (2008/2015/2021 vital stats all differ), so those
    need a (name, guide) composite key instead of colliding down to one row."""
    def dedup_key(item):
        name = (item.get(name_key) or '').strip().lower()
        if not name:
            return None
        if guide_key:
            return (name, (item.get(guide_key) or '').strip().lower())
        return name

    merged = {}
    for item in catalog_items:
        key = dedup_key(item)
        if key:
            merged[key] = item
    for item in custom_items:
        key = dedup_key(item)
        if key:
            merged[key] = item
    return list(merged.values())


def load_ingredient_database():
    """The full picobrew.com ingredient catalog (Fermentables/Hops/Yeasts/Adjuncts/
    WaterAmendments/BeerStyles) -- extracted directly from the live Crafter page's own
    in-memory ingredient lists, see app/static/data/picobrew_ingredients.json -- merged
    with any custom entries the user has added locally (via /new_ingredient) in
    app/recipes/ingredients/ingredients.json."""
    catalog_path = Path(current_app.root_path) / 'static' / 'data' / 'picobrew_ingredients.json'
    try:
        with open(catalog_path) as f:
            catalog = json.load(f)
    except Exception as e:
        current_app.logger.error("ERROR: could not load ingredient catalog {}".format(catalog_path))
        current_app.logger.error(e)
        catalog = {}

    custom = load_ingredients() or {}
    result = {
        'Fermentables': merge_ingredient_list(catalog.get('fermentables', []), custom.get('Fermentables', []), 'Name'),
        'Hops': merge_ingredient_list(catalog.get('hops', []), custom.get('Hops', []), 'Name'),
        'Yeast': merge_ingredient_list(catalog.get('yeasts', []), custom.get('Yeast', []), 'Name'),
        'Adjuncts': merge_ingredient_list(catalog.get('adjuncts', []), custom.get('Adjuncts', []), 'Name'),
        'WaterAmendments': merge_ingredient_list(catalog.get('amendments', []), custom.get('WaterAmendments', []), 'Name'),
        'BeerStyles': merge_ingredient_list(catalog.get('beerStyles', []), custom.get('BeerStyles', []), 'StyleNameCode', guide_key='StyleGuide'),
    }

    for f in result['Fermentables']:
        for c in LOVIBOND_COLOR_DATA:
            if float(f['Color']) >= c['Lower'] and float(f['Color']) < c['Upper']:
                f['ColorHEX'] = c['HEX']
                f['ColorCategory'] = c['Category']
                break

    result['Fermentables'].sort(key=lambda i: i.get('Name', ''))
    result['Hops'].sort(key=lambda i: i.get('Name', ''))
    result['Yeast'].sort(key=lambda i: (i.get('Laboratory') or '', i.get('Name', '')))
    result['Adjuncts'].sort(key=lambda i: i.get('Name', ''))
    result['WaterAmendments'].sort(key=lambda i: i.get('Name', ''))
    result['BeerStyles'].sort(key=lambda i: (int(i.get('CatNumCode') or 0), i.get('CatLettCode') or ''))

    return result


@main.route('/ingredients')
def _ingredients():
    global ingredients
    ingredients = load_ingredient_database()
    return render_template_with_defaults('ingredients.html', ingredients=ingredients, ingredient_table_ids=INGREDIENT_TABLE_IDS)


# Anchors matching each section's <table id="..."> in ingredients.html, so /new_ingredient
# can redirect back to the right section after saving.
INGREDIENT_TABLE_IDS = {
    'Fermentables': 'table-fermentables',
    'Hops': 'table-hops',
    'Yeast': 'table-yeasts',
    'Adjuncts': 'table-adjuncts',
    'WaterAmendments': 'table-amendments',
    'BeerStyles': 'table-styles',
}

INGREDIENT_DISPLAY_NAMES = {
    'Fermentables': 'Fermentable',
    'Hops': 'Hop',
    'Yeast': 'Yeast',
    'Adjuncts': 'Adjunct',
    'WaterAmendments': 'Water Amendment',
    'BeerStyles': 'Beer Style',
}

# Drives both the /new_ingredient form and its submission handling below -- one schema
# instead of six hand-written forms/parsers.
INGREDIENT_FIELDS = {
    'Fermentables': [
        {'key': 'Name', 'label': 'Name', 'type': 'text', 'required': True},
        {'key': 'FermentableType', 'label': 'Type', 'type': 'select',
         'options': ['Grain', 'Extract', 'DryExtract', 'Sugar', 'OtherGrain'], 'default': 'Grain'},
        {'key': 'Color', 'label': 'Color (°L)', 'type': 'number', 'step': '0.1', 'default': '0'},
        {'key': 'Yield', 'label': 'Yield (PPG)', 'type': 'number', 'step': '0.1', 'default': '0'},
        {'key': 'Supplier', 'label': 'Supplier', 'type': 'text'},
        {'key': 'Origin', 'label': 'Origin', 'type': 'text'},
    ],
    'Hops': [
        {'key': 'Name', 'label': 'Name', 'type': 'text', 'required': True},
        {'key': 'Alpha', 'label': 'Alpha (%)', 'type': 'number', 'step': '0.1', 'default': '0'},
    ],
    'Yeast': [
        {'key': 'Name', 'label': 'Name', 'type': 'text', 'required': True},
        {'key': 'Laboratory', 'label': 'Laboratory', 'type': 'text'},
        {'key': 'ProductID', 'label': 'Product ID', 'type': 'text'},
        {'key': 'MinAtten', 'label': 'Min Attenuation (%)', 'type': 'number', 'step': '0.1', 'default': '0'},
        {'key': 'MaxAtten', 'label': 'Max Attenuation (%)', 'type': 'number', 'step': '0.1', 'default': '0'},
        {'key': 'ExpectedAtten', 'label': 'Expected Attenuation (%)', 'type': 'number', 'step': '0.1', 'default': '0'},
        {'key': 'MinTemp', 'label': 'Min Temp (°F)', 'type': 'number', 'step': '1', 'default': '0'},
        {'key': 'MaxTemp', 'label': 'Max Temp (°F)', 'type': 'number', 'step': '1', 'default': '0'},
        {'key': 'ExpectedTemp', 'label': 'Pitch Temp (°F)', 'type': 'number', 'step': '1', 'default': '0'},
    ],
    'Adjuncts': [
        {'key': 'Name', 'label': 'Name', 'type': 'text', 'required': True},
        {'key': 'AdjunctType', 'label': 'Type', 'type': 'select',
         'options': ['Fining', 'Herb', 'Spice', 'Other'], 'default': 'Other'},
        {'key': 'Use', 'label': 'Use', 'type': 'text'},
    ],
    'WaterAmendments': [
        {'key': 'Name', 'label': 'Name', 'type': 'text', 'required': True},
        {'key': 'Description', 'label': 'Description', 'type': 'text'},
    ],
    'BeerStyles': [
        {'key': 'StyleNameCode', 'label': 'Style Name', 'type': 'text', 'required': True},
        {'key': 'CatNumCode', 'label': 'Category Number', 'type': 'text'},
        {'key': 'CatLettCode', 'label': 'Category Letter', 'type': 'text'},
        {'key': 'StyleGuide', 'label': 'Style Guide', 'type': 'text', 'default': 'Custom'},
        {'key': 'MinOG', 'label': 'Min OG', 'type': 'number', 'step': '0.001', 'default': '1.000'},
        {'key': 'MaxOG', 'label': 'Max OG', 'type': 'number', 'step': '0.001', 'default': '1.000'},
        {'key': 'MinFG', 'label': 'Min FG', 'type': 'number', 'step': '0.001', 'default': '1.000'},
        {'key': 'MaxFG', 'label': 'Max FG', 'type': 'number', 'step': '0.001', 'default': '1.000'},
        {'key': 'MinIBU', 'label': 'Min IBU', 'type': 'number', 'step': '1', 'default': '0'},
        {'key': 'MaxIBU', 'label': 'Max IBU', 'type': 'number', 'step': '1', 'default': '0'},
        {'key': 'MinSRM', 'label': 'Min SRM', 'type': 'number', 'step': '1', 'default': '0'},
        {'key': 'MaxSRM', 'label': 'Max SRM', 'type': 'number', 'step': '1', 'default': '0'},
        {'key': 'MinABV', 'label': 'Min ABV (%)', 'type': 'number', 'step': '0.1', 'default': '0'},
        {'key': 'MaxABV', 'label': 'Max ABV (%)', 'type': 'number', 'step': '0.1', 'default': '0'},
    ],
}


@main.route('/new_ingredient', methods=['GET', 'POST'])
def _new_ingredient():
    ingredient_type = request.values.get('type', '')
    fields = INGREDIENT_FIELDS.get(ingredient_type)
    if not fields:
        return 'Unknown ingredient type: {}'.format(ingredient_type), 404

    error = None
    if request.method == 'POST':
        data = {}
        for field in fields:
            raw = request.form.get(field['key'], '').strip()
            if field['type'] == 'number':
                try:
                    data[field['key']] = float(raw) if raw else float(field.get('default', 0))
                except ValueError:
                    data[field['key']] = float(field.get('default', 0))
            else:
                data[field['key']] = raw or field.get('default') or None

        name_key = INGREDIENT_NAME_KEYS[ingredient_type]
        if not data.get(name_key):
            name_label = next(f['label'] for f in fields if f['key'] == name_key)
            error = '{} is required.'.format(name_label)
        else:
            custom = load_ingredients() or {}
            custom.setdefault(ingredient_type, [])
            custom[ingredient_type].append(data)
            filepath = current_app.config['RECIPES_PATH'].joinpath('ingredients/ingredients.json')
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w') as f:
                json.dump(custom, f, indent=4, sort_keys=True)
            return redirect(url_for('main._ingredients') + '#' + INGREDIENT_TABLE_IDS[ingredient_type])

    display_name = INGREDIENT_DISPLAY_NAMES.get(ingredient_type, ingredient_type)
    return render_template_with_defaults('new_ingredient.html', ingredient_type=ingredient_type, display_name=display_name, fields=fields, error=error, form=request.form)

@main.route('/ingredient/edit/<id>', methods=['GET', 'POST'])
def _ingredient_edit(id):
    ##
    return render_template_with_defaults('ingredient_editor.html')

@main.route('/ingredient/clone/<id>', methods=['GET'])
def _ingredient_clone(id):
    # if request.method == 'DELETE':
    newID = uuid.uuid4().hex[:32]
    print(id + " : " + request.method)
    print("New ID:" + newID)
    return '', 204

@main.route('/ingredient/delete/<id>', methods=['GET'])
def _ingredient_delete(id):
    # if request.method == 'DELETE':
    print(id + " : " + request.method)
    return '', 204

#   Recipe: /API/pico/getRecipe?rfid={rfid}
# Response: HTML
get_recipe_args = {
    'rfid': fields.Str(required=True)      # 14 character alpha-numeric PicoPak RFID
}


def _recipe_list_dict(recipes):
    return [json.loads(json.dumps(recipe, default=lambda r: r.__dict__)) for recipe in recipes]


@main.route('/legacy_recipes')
def _legacy_recipes():
    """Combined Library page for all three legacy (pre-unified) recipe formats -- replaces
    the three separate Pico/Zymatic/ZSeries Recipes navbar dropdowns with sections on one
    page. The individual /pico_recipes, /zymatic_recipes, /zseries_recipes routes and
    templates are left in place (nothing else in the app assumes they're gone -- e.g.
    _recipe_edit's new_zymatic_recipe() fallback), just no longer linked from the navbar."""
    global pico_recipes, zymatic_recipes, zseries_recipes, invalid_recipes
    pico_recipes = load_pico_recipes()
    zymatic_recipes = load_zymatic_recipes()
    zseries_recipes = load_zseries_recipes()
    return render_template_with_defaults(
        'legacy_recipes.html',
        pico_recipes=_recipe_list_dict(pico_recipes),
        zymatic_recipes=_recipe_list_dict(zymatic_recipes),
        zseries_recipes=_recipe_list_dict(zseries_recipes),
        invalid_pico=invalid_recipes.get(MachineType.PICOBREW, set()),
        invalid_zymatic=invalid_recipes.get(MachineType.ZYMATIC, set()),
        invalid_zseries=invalid_recipes.get(MachineType.ZSERIES, set()))


@main.route('/legacy_import')
def _legacy_import():
    """Combined Import page for all three legacy recipe formats. Each section's form posts
    (via import_recipe.js) straight to the existing per-machine import endpoint
    (/import_pico_recipe etc.) -- those endpoints' POST handlers are unchanged, only their
    GET-rendered standalone page is being superseded as the linked entry point."""
    pico_machine_ids = [uid for uid in active_brew_sessions
                         if active_brew_sessions[uid].machine_type in [MachineType.PICOBREW, MachineType.PICOBREW_C]]
    zymatic_machine_ids = [uid for uid in active_brew_sessions
                            if active_brew_sessions[uid].machine_type == MachineType.ZYMATIC]
    zseries_machine_ids = [uid for uid in active_brew_sessions
                            if active_brew_sessions[uid].machine_type == MachineType.ZSERIES]
    return render_template_with_defaults(
        'legacy_import.html',
        pico_machine_ids=pico_machine_ids,
        zymatic_machine_ids=zymatic_machine_ids,
        zseries_machine_ids=zseries_machine_ids)


@main.route('/pico_recipes')
def _pico_recipes():
    global pico_recipes, invalid_recipes
    pico_recipes = load_pico_recipes()
    recipes_dict = [json.loads(json.dumps(recipe, default=lambda r: r.__dict__)) for recipe in pico_recipes]
    return render_template_with_defaults('pico_recipes.html', recipes=recipes_dict, invalid=invalid_recipes.get(MachineType.PICOBREW, set()))


def refresh_pico_recipe_cache():
    """The /API/pico device endpoints serve from these cached lists, so every write to a Pico
    recipe file must refresh them or devices keep seeing the old set until a restart."""
    global pico_recipes
    pico_recipes = load_pico_recipes()
    load_active_recipes(MachineType.PICOBREW)


@main.route('/new_pico_recipe', methods=['GET', 'POST'])
def new_pico_recipe():
    if request.method == 'POST':
        recipe = request.get_json()
        recipe['id'] = uuid.uuid4().hex[:14]
        filename = build_recipe_filename(recipe_path(MachineType.PICOBREW), recipe['name'])
        result = write_recipe_file(filename, recipe)
        refresh_pico_recipe_cache()
        return result
    else:
        return render_template_with_defaults('new_pico_recipe.html')


@main.route('/import_pico_recipe', methods=['GET', 'POST'])
def import_pico_recipe():
    if request.method == 'POST':
        data = request.get_json()
        rfid = data['rfid']  # picopak rfid
        uid = data['uid']  # picopak rfid
        try:
            # import for picobrew and picobrew_c are the same
            import_recipes(uid, None, rfid, MachineType.PICOBREW)
            refresh_pico_recipe_cache()
            return '', 204
        except Exception as e:
            current_app.logger.error(f'import of picopak recipe failed: {e}')
            return getattr(e, 'message', e.args[0]), 400
    else:
        machine_ids = [uid for uid in active_brew_sessions if active_brew_sessions[uid].machine_type in [MachineType.PICOBREW, MachineType.PICOBREW_C]]
        return render_template_with_defaults('import_brewhouse_recipe.html', rfid_required=True, machine_ids=machine_ids,
                                              post_url='/import_pico_recipe', redirect_url='/legacy_recipes', recipe_type='pico')


@main.route('/update_pico_recipe', methods=['POST'])
def update_pico_recipe():
    update_recipe = request.get_json()
    synced_files = list(recipe_path(MachineType.PICOBREW).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.PICOBREW, True).glob(file_glob_pattern))
    files = synced_files + archived_files
    for filename in files:
        recipe = load_pico_recipe(filename)
        if recipe.id == update_recipe['id']:
            recipe.update_recipe(filename, update_recipe)
    refresh_pico_recipe_cache()
    return '', 204


@main.route('/delete_pico_recipe', methods=['POST'])
def delete_pico_recipe():
    recipe_id = request.get_json()
    synced_files = list(recipe_path(MachineType.PICOBREW).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.PICOBREW, True).glob(file_glob_pattern))
    files = synced_files + archived_files
    for filename in files:
        recipe = load_pico_recipe(filename)
        if recipe.id == recipe_id:
            os.remove(filename)
            refresh_pico_recipe_cache()
            return '', 204
    return 'Delete Recipe: Failed to find recipe id "{recipe_id}"', 418


sync_recipe_args = {
    'recipe_type': fields.Str(required=True),        # type of recipe (pico, zymatic, zseries)
    'recipe_id': fields.Str(required=True),          # unique id of recipe file
}


@main.route('/sync_recipe', methods=['POST'])
@use_args(sync_recipe_args, location='querystring')
def sync_recipe(args):
    recipe_type = args['recipe_type']
    if recipe_type == "pico":
        mtype = MachineType.PICOBREW
    elif recipe_type == "zymatic":
        mtype = MachineType.ZYMATIC
    elif recipe_type == "zseries":
        mtype = MachineType.ZSERIES
    else:
        raise Exception("unsupported recipe_type {{args['recipe_type']}}")

    recipe_id = args['recipe_id']
    synced_files = list(recipe_path(mtype).glob(file_glob_pattern))
    archived_files = list(recipe_path(mtype, True).glob(file_glob_pattern))
    files = synced_files + archived_files
    for filename in files:
        recipe = load_recipe(filename, mtype)
        # zseries recipe.id is an integer, whereas pico and zymatic recipe.id is a string
        if str(recipe.id) == str(recipe_id):
            recipe.is_archived = not recipe.is_archived  # toggle archive/sync status
            recipe.sync_recipe(filename)
            load_active_recipes(mtype)
            return '', 204

    return 'could not sync - failed to find recipe.id {recipe_id}', 404


@main.route('/scanner')
def _scanner():
    return render_template_with_defaults('scanner.html')

def load_recipe(filename, mtype):
    if mtype in [MachineType.PICOBREW, MachineType.PICOBREW_C, MachineType.PICOBREW_C_ALT]:
        return load_pico_recipe(filename)
    elif mtype == MachineType.ZYMATIC:
        return load_zymatic_recipe(filename)
    elif mtype == MachineType.ZSERIES:
        return load_zseries_recipe(filename)
    raise Exception("invalid device type {mtype}")


def is_ajax(request):
    """
    This utility function is used, as `request.is_ajax()` is deprecated.

    This implements the previous functionality. Note that you need to
    attach this header manually if using fetch.
    """
    return request.headers.get('X_REQUESTED_WITH') == "XMLHttpRequest"


def load_redux_recipes(include_archived=True):
    synced_files = list(recipe_path(MachineType.UNIFIED).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.UNIFIED, True).glob(file_glob_pattern))

    files = synced_files
    if include_archived:
        files += archived_files

    current_app.logger.info(f'load_redux_recipes : {len(synced_files)} synced ; {len(archived_files)} archived ')
    recipes = [load_redux_recipe(file) for file in files]
    return list(sorted(filter(lambda x: x.name != None, recipes), key=lambda x: x.name))

def load_redux_recipe(file):
    recipe = ReduxRecipe()
    parse_recipe(MachineType.ZSERIES, recipe, file)

    recipe.name_escaped = escape(recipe.name).replace(" ", "_")
    return recipe


def find_redux_recipe_by_tag_id(tag_id, include_archived=False):
    """Shared by the real device-facing endpoints (routes_pico_api.py's getRecipe, mqtt.py's
    scan notification) and the Tag Scanner browser tool -- a unified recipe only matches a
    physical NFC tag scan via its own assigned TagID (recipe_editor.html's Tag Programming
    section), never its RecipeGUID. Defaults to active-only, matching get_pico_recipes(False)
    parity for what a real Pico may actually be served; the scanner tool passes
    include_archived=True since an archived match is still a useful diagnostic answer there."""
    if not tag_id:
        return None
    return next((r for r in load_redux_recipes(include_archived) if r.TagID == tag_id), None)

def load_pico_recipes(include_archived=True):
    synced_files = list(recipe_path(MachineType.PICOBREW).glob(file_glob_pattern))
    archived_files = list(recipe_path(MachineType.PICOBREW, True).glob(file_glob_pattern))

    files = synced_files
    if include_archived:
        files += archived_files

    current_app.logger.info(f'load_pico_recipes : {len(synced_files)} synced ; {len(archived_files)} archived ')
    recipes = [load_pico_recipe(file) for file in files]
    return list(sorted(filter(lambda x: x.name != None, recipes), key=lambda x: x.name))


def load_pico_recipe(file):
    recipe = PicoBrewRecipe()
    parse_recipe(MachineType.PICOBREW, recipe, file)

    recipe.name_escaped = escape(recipe.name).replace(" ", "_")
    return recipe


def get_pico_recipes(archive_included=True):
    global pico_recipes
    global pico_active_recipes
    return pico_recipes if archive_included else pico_active_recipes


def build_recipe_filename(recipe_path, recipe_name):
    return recipe_path.joinpath('{}.json'.format(recipe_name.strip().replace(' ', '_')))


def write_recipe_file(filename, recipe):
    if not filename.exists():
        with open(filename, "w") as file:
            json.dump(recipe, file, indent=4, sort_keys=True)
        return '', 204
    else:
        return 'Recipe Exists!', 418


def load_active_brew_sessions():
    brew_sessions = []

    # process brew_sessions from memory
    for uid in active_brew_sessions:
        # current_app.logger.debug(f'date : {active_brew_sessions[uid].created_at}')
        session_data = active_brew_sessions[uid].data
        brew_session = {'alias': active_brew_sessions[uid].alias,
                              'uid': uid,
                              'active': active_brew_sessions[uid].name != 'Waiting To Brew',
                              'date': active_brew_sessions[uid].created_at or None,
                              'machine_type': active_brew_sessions[uid].machine_type,
                              'graph': get_brew_graph_data(uid, active_brew_sessions[uid].name,
                                                           active_brew_sessions[uid].step,
                                                           active_brew_sessions[uid].data,
                                                           active_brew_sessions[uid].is_pico)}

        if len(session_data) > 0:
            if 'timeLeft' in session_data[-1]:
                brew_session.update({'time_remaining': timedelta(seconds=session_data[-1]['timeLeft'])})

        # include last session info and num dirty sessions since last clean
        dirty_since_clean = dirty_sessions_since_clean(uid, active_brew_sessions[uid].machine_type)
        # current_app.logger.error("ERROR: Num dirty sessions {}".format(dirty_since_clean))
        last_session_type, last_session_name = last_session_metadata(uid, active_brew_sessions[uid].machine_type)
        brew_session.update({
            'last_session': {
                'type': BrewSessionType(last_session_type.name).name,
                'name': last_session_name,
            },
            'dirty_sessions_since_clean': dirty_since_clean
        })

        brew_sessions.append(brew_session)
    return brew_sessions


def parse_ferm_session(file):
    try:
        return load_ferm_session(file)
    except Exception:
        current_app.logger.error("ERROR: An exception occurred parsing {}".format(file))
        add_invalid_session("ferm", file)


def load_active_ferm_sessions():
    ferm_sessions = []
    for uid in active_ferm_sessions:
        ferm_sessions.append({'alias': active_ferm_sessions[uid].alias,
                              'uid': uid,
                              'active': active_ferm_sessions[uid].active,
                              'date': active_ferm_sessions[uid].start_time or None,
                              'graph': get_ferm_graph_data(uid, active_ferm_sessions[uid].voltage,
                                                           active_ferm_sessions[uid].data)})
    return ferm_sessions


def load_ferm_sessions(uid=None, offset=0, limit=None):
    files = list_session_files(ferm_archive_sessions_path(), uid)

    ferm_sessions = [parse_ferm_session(file) for file in files]
    ferm_sessions = list(filter(lambda x: x != None, ferm_sessions))

    return _paginate_sessions(ferm_sessions, offset, limit)


def parse_still_session(file):
    try:
        return load_still_session(file)
    except Exception:
        current_app.logger.error("ERROR: An exception occurred parsing {}".format(file))
        add_invalid_session("still", file)


def load_active_still_sessions():
    still_sessions = []
    for uid in active_still_sessions:
        still_sessions.append({'alias': active_still_sessions[uid].alias,
                              'uid': uid,
                              'ip_address': active_still_sessions[uid].ip_address,
                              'active': active_still_sessions[uid].active,
                              'date': active_still_sessions[uid].created_at or None,
                              'graph': get_still_graph_data(uid, active_still_sessions[uid].name, active_still_sessions[uid].data)})
    return still_sessions


def load_still_sessions(uid=None, offset=0, limit=None):
    files = list_session_files(still_archive_sessions_path(), uid)

    still_sessions = [parse_still_session(file) for file in sorted(files, reverse=True)]
    still_sessions = list(filter(lambda x: x != None, still_sessions))

    return _paginate_sessions(still_sessions, offset, limit)


def parse_iSpindel_session(file):
    try:
        return load_iSpindel_session(file)
    except:
        current_app.logger.error("ERROR: An exception occurred parsing {}".format(file))
        add_invalid_session("iSpindel", file)


def load_active_iSpindel_sessions():
    iSpindel_sessions = []
    for uid in active_iSpindel_sessions:
        iSpindel_sessions.append({'alias': active_iSpindel_sessions[uid].alias,
                                  'uid': uid,
                                  'active': active_iSpindel_sessions[uid].active,
                                  'date': active_iSpindel_sessions[uid].start_time or None,
                                  'graph': get_iSpindel_graph_data(uid, active_iSpindel_sessions[uid].voltage,
                                                                   active_iSpindel_sessions[uid].data)})
    return iSpindel_sessions


def load_iSpindel_sessions(uid=None, offset=0, limit=None):
    files = list_session_files(iSpindel_archive_sessions_path(), uid)
    iSpindel_sessions = [parse_iSpindel_session(file) for file in sorted(files, reverse=True)]
    iSpindel_sessions = list(filter(lambda x: x != None, iSpindel_sessions))

    return _paginate_sessions(iSpindel_sessions, offset, limit)


def parse_tilt_session(file):
    try:
        return load_tilt_session(file)
    except Exception:
        current_app.logger.error("ERROR: An exception occurred parsing {}".format(file))
        add_invalid_session("tilt", file)


def load_active_tilt_sessions():
    tilt_sessions = []
    for uid in active_tilt_sessions:
        tilt_sessions.append({'alias': active_tilt_sessions[uid].alias,
                                  'uid': uid,
                                  'color': active_tilt_sessions[uid].color,
                                  'active': active_tilt_sessions[uid].active,
                                  'date': active_tilt_sessions[uid].start_time or None,
                                  'graph': get_tilt_graph_data(uid, active_tilt_sessions[uid].rssi,
                                                                   active_tilt_sessions[uid].data)})
    return tilt_sessions


def load_tilt_sessions(uid=None, offset=0, limit=None):
    files = list_session_files(tilt_archive_sessions_path(), uid)

    tilt_sessions = [parse_tilt_session(file) for file in sorted(files, reverse=True)]
    tilt_sessions = list(filter(lambda x: x != None, tilt_sessions))

    return _paginate_sessions(tilt_sessions, offset, limit)


# Read initial recipe list on load
pico_recipes, pico_active_recipes = [], []
zymatic_recipes, zymatic_active_recipes = [], []
zseries_recipes, zseries_active_recipes = [], []

brew_sessions = []
ferm_sessions = []
still_sessions = []
iSpindel_sessions = []
tilt_sessions = []

invalid_recipes = {}
invalid_sessions = {}


def initialize_data():
    global pico_recipes, zymatic_recipes, zseries_recipes, invalid_recipes
    global pico_active_recipes, zymatic_active_recipes, zseries_active_recipes, invalid_recipes
    global brew_sessions, ferm_sessions, still_sessions, iSpindel_sessions, tilt_sessions

    # Read initial recipe list on load
    pico_recipes = load_pico_recipes()
    zymatic_recipes = load_zymatic_recipes()
    zseries_recipes = load_zseries_recipes()

    # Read initial active recipe list on load
    load_active_recipes(None)

    # load all archive brew sessions
    brew_sessions = load_active_brew_sessions()
    ferm_sessions = load_active_ferm_sessions()
    still_sessions = load_active_still_sessions()
    iSpindel_sessions = load_active_iSpindel_sessions()
    tilt_sessions = load_active_tilt_sessions()


def load_active_recipes(mtype):
    global pico_active_recipes, zymatic_active_recipes, zseries_active_recipes

    if mtype in [MachineType.PICOBREW, MachineType.PICOBREW_C, MachineType.PICOBREW_C_ALT]:
        pico_active_recipes = load_pico_recipes(False)
    elif mtype == MachineType.ZYMATIC:
        zymatic_active_recipes = load_zymatic_recipes(False)
    elif mtype == MachineType.ZSERIES:
        zseries_active_recipes = load_zseries_recipes(False)
    elif mtype == None:
        pico_active_recipes = load_pico_recipes(False)
        zymatic_active_recipes = load_zymatic_recipes(False)
        zseries_active_recipes = load_zseries_recipes(False)


# utilities
def increment_zseries_recipe_id():
    recipe_id = 1
    found = False

    recipe_ids = [r.id for r in get_zseries_recipes()]
    while recipe_id in recipe_ids:
        recipe_id += 1

    return recipe_id
