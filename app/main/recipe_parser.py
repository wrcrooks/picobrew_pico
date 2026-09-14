import json
import os
import uuid

from .config import recipe_path
from .model import PICO_LOCATION, ZYMATIC_LOCATION, ZSERIES_LOCATION, MachineType
from flask import current_app


class ZymaticRecipeStep():
    def __init__(self):
        self.name = None
        self.temperature = None
        self.step_time = None
        self.location = None
        self.drain_time = None

    def serialize(self):
        return '{0},{1},{2},{3},{4}/'.format(
            self.name,
            self.temperature,
            self.step_time,
            ZYMATIC_LOCATION[self.location],
            self.drain_time
        )


class ZymaticRecipe():
    def __init__(self):
        self.clean = False
        self.id = None
        self.name = None
        self.name_ = None
        self.notes = None
        self.is_archived = False
        self.steps = []

    def parse(self, file):
        recipe = None
        with open(file) as f:
            recipe = json.load(f)
        self.clean = recipe.get('clean', False) or False
        self.id = recipe.get('id', 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX') or 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'
        self.name = recipe.get('name', 'Empty Recipe') or 'Empty Recipe'
        self.name_ = self.name.replace(" ", "_").replace("\'", "")
        self.notes = recipe.get('notes', None) or None
        self.is_archived = "/archive/" in str(file)
        if 'steps' in recipe:
            for recipe_step in recipe['steps']:
                step = ZymaticRecipeStep()
                step.name = recipe_step.get('name', 'Empty Step') or 'Empty Step'
                step.temperature = 70 if 'temperature' not in recipe_step else int(recipe_step['temperature'])
                step.step_time = 0 if 'step_time' not in recipe_step else int(recipe_step['step_time'])
                step.location = recipe_step.get('location', 'PassThru') or 'PassThru'
                if step.location not in ZYMATIC_LOCATION:
                    raise ValueError(f'step.location provided {step.location} is not supported by Zymatic machine type')
                step.drain_time = 0 if 'drain_time' not in recipe_step else int(recipe_step['drain_time'])
                self.steps.append(step)

    def serialize(self):
        steps = map(lambda step: step.serialize(), self.steps)
        return '{0}/{1}/{2}|'.format(
            self.name,
            self.id,
            ''.join(steps)
        )

    def sync_recipe(self, filename):
        old_recipe_file = filename
        filename = recipe_path(MachineType.ZYMATIC, self.is_archived).joinpath('{}.json'.format(self.name_))
        os.rename(old_recipe_file, filename)

    def update_recipe(self, filename, recipe):
        old_recipe_file = None
        if (self.name and self.name != recipe.get('name', self.name)):
            self.name = recipe.get('name', 'Empty Recipe')
            self.name_ = self.name.replace(" ", "_").replace("\'", "")
            old_recipe_file = filename
            filename = recipe_path(MachineType.ZYMATIC, recipe.get('is_archived')).joinpath('{}.json'.format(self.name_))

        self.notes = recipe.get('notes', self.notes)
        self.steps = []
        for s in recipe.get('steps', self.steps):
            step = ZymaticRecipeStep()
            step.name = s.get('name', 'Empty Step') or 'Empty Step'
            step.temperature = 70 if 'temperature' not in s else int(s['temperature'])
            step.step_time = 0 if 'step_time' not in s else int(s['step_time'])
            step.location = s.get('location', 'PassThru') or 'PassThru'
            if step.location not in ZYMATIC_LOCATION:
                raise ValueError(f'step.location provided {step.location} is not supported by Zymatic machine type')
            step.drain_time = 0 if 'drain_time' not in s else int(s['drain_time'])
            self.steps.append(step)
        updated_recipe = json.loads(json.dumps(self, default=lambda r: r.__dict__))
        del updated_recipe['name_']

        with open(filename, 'w') as f:
            json.dump(updated_recipe, f, indent=4, sort_keys=True)

        if (old_recipe_file):
            os.remove(old_recipe_file)


def ZymaticRecipeImport(recipes):
    for recipe in recipes.strip('#|').split('|'):
        r = {}
        steps = list(filter(None, recipe.split('/')))
        r['name'] = steps.pop(0)
        r['clean'] = False
        r['id'] = steps.pop(0)
        r['steps'] = []
        for step in steps:
            values = step.split(',')
            s = {}
            s['name'] = values[0]
            s['temperature'] = int(values[1])
            s['step_time'] = int(values[2])
            s['location'] = next(k for k, v in ZYMATIC_LOCATION.items() if v == values[3])
            s['drain_time'] = int(values[4])
            r['steps'].append(s)
        filename = recipe_path(MachineType.ZYMATIC).joinpath('{}.json'.format(r['name'].replace(' ', '_')))
        if not filename.exists():
            with open(filename, "w") as file:
                json.dump(r, file, indent=4, sort_keys=True)


class ZSeriesRecipeStep():
    def __init__(self):
        self.name = None
        self.temperature = None
        self.step_time = None
        self.location = None
        self.drain_time = None

    def serialize(self):
        step = {}
        step['Name'] = self.name
        step['Location'] = int(ZSERIES_LOCATION[self.location])
        step['Temp'] = int(self.temperature)
        step['Time'] = int(self.step_time)
        step['Drain'] = int(self.drain_time)
        return step


class ZSeriesRecipe():
    def __init__(self):
        self.id = None
        self.name = None
        self.name_ = None
        self.notes = None
        self.start_water = 13.1
        self.kind_code = 0
        self.type_code = None
        self.is_archived = False
        self.steps = []

    def parse(self, file):
        recipe = None
        with open(file) as f:
            recipe = json.load(f)
        # TODO should this just fail or increment the number to be unique?
        self.id = recipe.get('id', 0) or 0
        self.name = recipe.get('name', 'Empty Recipe') or 'Empty Recipe'
        self.name_ = self.name.replace(" ", "_").replace("\'", "")
        self.notes = recipe.get('notes', None) or None
        self.start_water = recipe.get('start_water', 13.1) or 13.1
        self.type_code = recipe.get('type_code', "Beer") or "Beer"
        self.is_archived = "/archive/" in str(file)
        if 'steps' in recipe:
            for recipe_step in recipe['steps']:
                step = ZSeriesRecipeStep()
                step.name = recipe_step.get('name', 'Empty Step') or 'Empty Step'
                step.temperature = 70 if 'temperature' not in recipe_step else int(recipe_step['temperature'])
                step.step_time = 0 if 'step_time' not in recipe_step else int(recipe_step['step_time'])
                step.location = recipe_step.get('location', 'PassThru') or 'PassThru'
                if step.location == 'PassThrough':
                    step.location = 'PassThru'
                if step.location not in ZSERIES_LOCATION:
                    raise ValueError(f'step.location provided {step.location} is not supported by ZSeries machine type')
                step.drain_time = 0 if 'drain_time' not in recipe_step else int(recipe_step['drain_time'])
                self.steps.append(step)

    def serialize(self):
        r = {}
        r['ID'] = self.id
        r['Name'] = self.name
        r['StartWater'] = self.start_water
        r['Steps'] = []
        for step in self.steps:
            r['Steps'].append(step.serialize())
        return r

    def sync_recipe(self, filename):
        old_recipe_file = filename
        filename = recipe_path(MachineType.ZSERIES, self.is_archived).joinpath('{}.json'.format(self.name_))
        os.rename(old_recipe_file, filename)

    def update_recipe(self, filename, recipe):
        old_recipe_file = None
        if (self.name and self.name != recipe.get('name', self.name)):
            self.name = recipe.get('name', 'Empty Recipe')
            self.name_ = self.name.replace(" ", "_").replace("\'", "")
            old_recipe_file = filename
            filename = recipe_path(MachineType.ZSERIES, recipe.get('is_archived')).joinpath('{}.json'.format(self.name_))

        self.notes = recipe.get('notes', self.notes)
        self.steps = []
        for s in recipe.get('steps', self.steps):
            step = ZSeriesRecipeStep()
            step.name = s.get('name', 'Empty Step') or 'Empty Step'
            step.temperature = 70 if 'temperature' not in s else int(s['temperature'])
            step.step_time = 0 if 'step_time' not in s else int(s['step_time'])
            step.location = s.get('location', 'PassThru') or 'PassThru'
            if step.location == 'PassThrough':
                step.location = 'PassThru'
            if step.location not in ZSERIES_LOCATION:
                raise ValueError(f'step.location provided {step.location} is not supported by ZSeries machine type')
            step.drain_time = 0 if 'drain_time' not in s else int(s['drain_time'])
            self.steps.append(step)
        updated_recipe = json.loads(json.dumps(self, default=lambda r: r.__dict__))
        del updated_recipe['name_']
        del updated_recipe['kind_code']
        del updated_recipe['type_code']

        with open(filename, 'w') as f:
            json.dump(updated_recipe, f, indent=4, sort_keys=True)

        if (old_recipe_file):
            os.remove(old_recipe_file)


def ZSeriesRecipeImport(recipe):
    r = {}
    r['name'] = recipe['Name']
    name = recipe['Name']

    current_app.logger.debug(f'saving recipe {name}')

    r['clean'] = False
    # verify id is unique
    r['id'] = recipe['ID']
    r['start_water'] = recipe['StartWater']
    r['steps'] = []

    for step in recipe['Steps']:
        s = {}
        s['name'] = step['Name']
        s['temperature'] = step['Temp']
        s['step_time'] = step['Time']
        s['location'] = next(k for k, v in ZSERIES_LOCATION.items() if int(v) == step['Location'])
        s['drain_time'] = step['Drain']
        r['steps'].append(s)

    # This will cause a circular import
    # Perhaps re-factor "live state" out into its own module?
    # existing = load_zseries_recipes()
    # conflict = [old.id for old in existing if old.id == r['id']]
    # if conflict:
    #    current_app.logger.error(f'Conflicting Z-series recipe IDs: {conflicts}')
    #    return None

    recipe_name = r['name'].replace(' ', '_')
    filename = recipe_path(MachineType.ZSERIES).joinpath(f'{recipe_name}.json')
    if not filename.exists():
        with open(filename, "w") as file:
            json.dump(r, file, indent=4, sort_keys=True)


class PicoBrewRecipeStep():
    def __init__(self):
        self.name = None
        self.location = None
        self.temperature = None
        self.step_time = None
        self.drain_time = None

    def serialize(self):
        return '{0},{1},{2},{3},{4},'.format(
            self.temperature,
            self.step_time,
            self.drain_time,
            PICO_LOCATION[self.location],
            self.name
        )


class PicoBrewRecipe():
    def __init__(self):
        self.id = None
        self.name = None
        self.notes = None
        self.name_ = None
        self.abv_tweak = None
        self.ibu_tweak = None
        self.abv = None
        self.ibu = None
        self.is_pico = True
        self.is_archived = False
        self.image = None
        self.steps = []

    def parse(self, file):
        recipe = None
        with open(file) as f:
            recipe = json.load(f)
        self.id = recipe.get('id', 'XXXXXXXXXXXXXX') or 'XXXXXXXXXXXXXX'
        self.name = recipe.get('name', 'Empty Recipe') or 'Empty Recipe'
        self.name_ = self.name.replace(" ", "_").replace("\'", "")
        self.notes = recipe.get('notes', None) or None
        self.abv_tweak = recipe.get('abv_tweak', -1) or -1
        self.ibu_tweak = recipe.get('ibu_tweak', -1) or -1
        self.abv = recipe.get('abv', 6) or 6
        self.ibu = recipe.get('ibu', 40) or 40
        self.image = recipe.get('image', '') or ''
        self.is_archived = "/archive/" in str(file)
        if 'steps' in recipe:
            for recipe_step in recipe['steps']:
                step = PicoBrewRecipeStep()
                step.name = recipe_step.get('name', 'Empty Step') or 'Empty Step'
                step.location = recipe_step.get('location', 'PassThru') or 'PassThru'
                if step.location not in PICO_LOCATION:
                    raise ValueError(f'step.location provided {step.location} is not supported by Pico machine type')
                step.temperature = 70 if 'temperature' not in recipe_step else int(recipe_step['temperature'])
                step.step_time = 0 if 'step_time' not in recipe_step else int(recipe_step['step_time'])
                step.drain_time = 0 if 'drain_time' not in recipe_step else int(recipe_step['drain_time'])
                self.steps.append(step)

    def serialize(self):
        steps = map(lambda step: step.serialize(), self.steps)
        return '{0}/{1},{2},{3},{4},{5}|{6}|'.format(
            self.name,
            self.abv_tweak,
            self.ibu_tweak,
            self.abv,
            self.ibu,
            ''.join(steps),
            self.image
        )

    def sync_recipe(self, filename):
        old_recipe_file = filename
        filename = recipe_path(MachineType.PICOBREW, self.is_archived).joinpath('{}.json'.format(self.name_))
        os.rename(old_recipe_file, filename)

    def update_recipe(self, filename, recipe):
        old_recipe_file = None
        if (self.name and self.name != recipe.get('name', self.name)):
            self.name = recipe.get('name', 'Empty Recipe')
            self.name_ = self.name.replace(" ", "_").replace("\'", "")
            old_recipe_file = filename
            filename = recipe_path(MachineType.PICOBREW, recipe.get('is_archived')).joinpath('{}.json'.format(self.name_))

        self.abv = float(recipe.get('abv', self.abv) or 6)
        self.ibu = float(recipe.get('ibu', self.ibu) or 40)
        self.image = recipe.get('image', self.image)
        self.notes = recipe.get('notes', self.notes)
        self.steps = []
        for s in recipe.get('steps', self.steps):
            step = PicoBrewRecipeStep()
            step.name = s.get('name', 'Empty Step') or 'Empty Step'
            step.location = s.get('location', 'PassThru') or 'PassThru'
            if step.location not in PICO_LOCATION:
                raise ValueError(f'step.location provided {step.location} is not supported by Pico machine type')
            step.temperature = 70 if 'temperature' not in s else int(s['temperature'])
            step.step_time = 0 if 'step_time' not in s else int(s['step_time'])
            step.drain_time = 0 if 'drain_time' not in s else int(s['drain_time'])
            self.steps.append(step)
        updated_recipe = json.loads(json.dumps(self, default=lambda r: r.__dict__))
        del updated_recipe['name_']

        with open(filename, 'w') as f:
            json.dump(updated_recipe, f, indent=4, sort_keys=True)

        if (old_recipe_file):
            os.remove(old_recipe_file)


def PicoBrewRecipeImport(recipe, rfid=None):
    r = {}
    r['id'] = uuid.uuid4().hex[:14] if rfid is None else rfid
    tmp = recipe.strip('#|').split('|')
    if len(tmp) > 1:
        r['image'] = tmp[1]
    tmp = tmp[0].split('/')
    steps = list(filter(None, tmp.pop().split(',')))
    r['name'] = ''.join(tmp)
    r['abv_tweak'] = steps.pop(0)
    r['ibu_tweak'] = steps.pop(0)
    r['abv'] = steps.pop(0)
    r['ibu'] = steps.pop(0)
    r['steps'] = []
    for step in [steps[i:i + 5] for i in range(0, len(steps), 5)]:
        s = {}
        s['temperature'] = int(step[0])
        s['step_time'] = int(step[1])
        s['drain_time'] = int(step[2])
        s['location'] = next(k for k, v in PICO_LOCATION.items() if v == step[3])
        s['name'] = step[4]
        r['steps'].append(s)
    filename = recipe_path(MachineType.PICOBREW).joinpath('{}.json'.format(r['name'].replace(' ', '_')))
    if not filename.exists():
        with open(filename, "w") as file:
            json.dump(r, file, indent=4, sort_keys=True)


class ReduxRecipe():
    def __init__(self):
        self.id = None
        self.name = None
        self.notes = None
        self.name_ = None
        self.abv_tweak = None
        self.ibu_tweak = None
        self.abv = None
        self.ibu = None
        self.is_pico = True
        self.is_archived = False
        self.image = None
        self.steps = []
        self.BrewingInstructionsText = None
        self.FermentationInstructionsText = None
        self._raw = None
        self.filepath = None

    def parse(self, file):
        recipe = None
        with open(file) as f:
            recipe = json.load(f)
        self._raw = recipe
        self.filepath = str(file)
        self.BrewingInstructionsText = recipe['VM']['Recipe'].get('BrewingInstructionsText', None)
        self.FermentationInstructionsText = recipe['VM']['Recipe'].get('FermentationInstructionsText', None)
        self.id = recipe.get('RecipeGUID', 'XXXXXXXXXXXXXX') or 'XXXXXXXXXXXXXX'
        self.name = recipe['VM']['Recipe']['Name'] or 'Empty Recipe'
        self.name_ = self.name.replace(" ", "_").replace("\'", "")
        self.notes = recipe.get('notes', None) or None
        self.abv_tweak = recipe.get('abv_tweak', -1) or -1
        self.ibu_tweak = recipe.get('ibu_tweak', -1) or -1
        self.abv = recipe['VM']['Recipe']['ABV'] or 6.0
        self.ibu = recipe.get('ibu', 40) or 40
        self.image = recipe.get('image', '') or ''
        self.is_archived = "/archive/" in str(file)
        self.author = recipe['VM']['Recipe']['Author'] or 'Unattributed'
        self.OG = recipe['VM']['Recipe']['OG'] or 1.050
        self.FG = recipe['VM']['Recipe']['FG'] or 1.010
        self.SRM = recipe['VM']['Recipe']['SRM'] or 10.0
        self.CreationDate = recipe['VM']['Recipe']['CreationDate'] or "2000-01-28T17:31:29.127"
        self.TastingNotes = recipe['VM']['Recipe']['TastingNotes'] or "No Tasting Notes"
        self.BeerStyle = recipe['VM']['Recipe']['BeerStyle'] or []
        self.UseMetric = recipe['UseMetric'] or False
        self.Fermentables = recipe['VM']['Recipe']['Fermentables'] or []
        self.Hops = recipe['VM']['Recipe']['Hops'] or []
        self.MachineSteps = recipe['VM']['Recipe']['MachineSteps'] or []
        self.WaterStats = recipe['VM']['Content']['Sections'][0]['Stats'] or []
        self.WaterAmendments = recipe['VM']['Recipe']['Amendments'] or []
        self.MashType = recipe['VM']['Recipe']['MashType'] or 0
        self.MashSteps = recipe['VM']['Recipe']['MashSteps'] or []
        self.BoilSteps = recipe['VM']['Recipe']['BoilSteps'] or []
        self.IsFirstWort = str(recipe['VM']['Recipe']['IsFirstWort']) or 'False'
        self.WhirlpoolSteps = recipe['VM']['Recipe']['WhirlpoolSteps'] or []
        self.WhirlpoolHops = recipe['VM']['Recipe']['WhirlpoolHops'] or []
        self.Yeast = recipe['VM']['Recipe']['Yeast'] or []
        self.FermentationSteps = recipe['VM']['Recipe']['FermentationSteps'] or []
        self.DryHops = recipe['VM']['Recipe']['DryHops'] or []
        self.HumanBrewingSteps = recipe['VM']['Recipe']['HumanBrewingSteps'] or []
        self.SpecialBrewingInstructions = recipe['VM']['Content']['SpecialBrewingInstructions'] or ""
        self.StyleNameCode = recipe['VM']['Recipe']['BeerStyle']['StyleNameCode'] or "Custom"
        try:
            self.Machine = recipe['Machine'] or "Custom"
        except Exception:
            self.Machine = "Pico Z" #TODO: Replace this with "Custom" to signify no machine specification
        # if 'steps' in recipe:
        #     for recipe_step in recipe['steps']:
        #         step = PicoBrewRecipeStep()
        #         step.name = recipe_step.get('name', 'Empty Step') or 'Empty Step'
        #         step.location = recipe_step.get('location', 'PassThru') or 'PassThru'
        #         if step.location not in PICO_LOCATION:
        #             raise ValueError(f'step.location provided {step.location} is not supported by Pico machine type')
        #         step.temperature = 70 if 'temperature' not in recipe_step else int(recipe_step['temperature'])
        #         step.step_time = 0 if 'step_time' not in recipe_step else int(recipe_step['step_time'])
        #         step.drain_time = 0 if 'drain_time' not in recipe_step else int(recipe_step['drain_time'])
        #         self.steps.append(step)

    def update_from_form(self, form):
        """Apply edits posted from recipe_editor.html onto the original raw recipe
        JSON (self._raw) and write it back to disk. Mutating the raw dict in place
        (rather than reconstructing it from the flattened attributes) preserves any
        fields the editor doesn't expose."""
        def to_float(value, default):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def to_int(value, default):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return default

        def rebuild_rows(table_key, field_casts):
            """Reconstruct a table's row list purely from submitted form data, so
            rows added/removed/reordered client-side (recipe_editor.js) come back
            exactly as the browser sent them -- there's no assumption the row count
            or order matches the original data. Each row's fields the editor doesn't
            expose ride along in a hidden '<table>.<i>.__extra' JSON blob (the row's
            original data, or '{}' for a brand-new row) so they survive a save even
            though only the visibly-editable fields are posted individually.

            If the table's '<table>.__present' marker is missing entirely -- a
            partial/malformed POST, not a real page submission -- leave the table
            untouched rather than silently wiping it to an empty list."""
            if f'{table_key}.__present' not in form:
                return r.get(table_key, [])
            rows = []
            i = 0
            while f'{table_key}.{i}.__extra' in form:
                try:
                    row = json.loads(form.get(f'{table_key}.{i}.__extra') or '{}')
                    if not isinstance(row, dict):
                        row = {}
                except ValueError:
                    row = {}
                for field, cast in field_casts.items():
                    posted = form.get(f'{table_key}.{i}.{field}')
                    if posted is None:
                        continue
                    row[field] = cast(posted, row.get(field)) if cast else posted
                rows.append(row)
                i += 1
            return rows

        r = self._raw['VM']['Recipe']
        content = self._raw['VM']['Content']

        new_name = form.get('Name', '').strip() or r.get('Name') or 'Empty Recipe'
        r['Name'] = new_name
        r['BeerStyle']['StyleNameCode'] = form.get('StyleNameCode', r['BeerStyle'].get('StyleNameCode'))
        r['TastingNotes'] = form.get('TastingNotes', r.get('TastingNotes', ''))
        r['BrewingInstructionsText'] = form.get('BrewingInstructionsText', r.get('BrewingInstructionsText', ''))
        r['FermentationInstructionsText'] = form.get('FermentationInstructionsText', r.get('FermentationInstructionsText', ''))
        content['SpecialBrewingInstructions'] = form.get('SpecialBrewingInstructions', content.get('SpecialBrewingInstructions', ''))

        r['MashSteps'] = rebuild_rows('MashSteps', {'Name': None, 'Temp': to_float, 'Time': to_float})
        r['Fermentables'] = rebuild_rows('Fermentables', {'Name': None, 'Amount': to_float, 'ColorPts': to_float})
        r['BoilSteps'] = rebuild_rows('BoilSteps', {'Location': to_int, 'Temp': to_float, 'Time': to_float})
        r['Hops'] = rebuild_rows('Hops', {'Name': None, 'Amount': to_float, 'Alpha': to_float, 'Time': to_float})
        r['WhirlpoolSteps'] = rebuild_rows('WhirlpoolSteps', {'Location': to_int, 'Temp': to_float, 'Time': to_float})
        r['WhirlpoolHops'] = rebuild_rows('WhirlpoolHops', {'Name': None, 'Amount': to_float, 'Alpha': to_float, 'Time': to_float})
        r['DryHops'] = rebuild_rows('DryHops', {'Name': None, 'Amount': to_float, 'Alpha': to_float, 'Time': to_float})
        r['Amendments'] = rebuild_rows('Amendments', {'Name': None, 'Amount': to_float, 'Units': None})
        r['FermentationSteps'] = rebuild_rows('FermentationSteps', {'Name': None, 'Temp': to_float, 'Days': to_float, 'Hours': to_float})
        # Temperature/Time/Drain must stay ints: routes_frontend.py builds the wort curve
        # via range(s['Time']), which raises TypeError on a float.
        r['MachineSteps'] = rebuild_rows('MachineSteps', {'Name': None, 'StepLocation': to_int,
                                                           'Temperature': to_int, 'Time': to_int, 'Drain': to_int})

        if r.get('Yeast'):
            y = r['Yeast']
            y['Name'] = form.get('Yeast.Name', y.get('Name'))
            y['ExpectedAtten'] = to_float(form.get('Yeast.ExpectedAtten'), y.get('ExpectedAtten'))
            y['MinTemp'] = to_float(form.get('Yeast.MinTemp'), y.get('MinTemp'))
            y['MaxTemp'] = to_float(form.get('Yeast.MaxTemp'), y.get('MaxTemp'))
            y['ExpectedTemp'] = to_float(form.get('Yeast.ExpectedTemp'), y.get('ExpectedTemp'))

        filename = self.filepath
        if new_name != self.name:
            new_filename = str(recipe_path(MachineType.UNIFIED, self.is_archived).joinpath(
                '{}.json'.format(new_name.strip().replace(' ', '_').replace("'", ""))))
            os.rename(filename, new_filename)
            filename = new_filename
            self.filepath = filename

        with open(filename, 'w') as out:
            json.dump(self._raw, out, indent=4, sort_keys=True)

        self.name = new_name