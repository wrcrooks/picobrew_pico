"""Predicts how a recipe's OG/FG/IBU/SRM/ABV move under different brewing conditions, for the
recipe viewer's sensitivity charts.

Every prediction is anchored to the recipe's own stored per-ingredient contributions (the same
sums recipe_calculations.js uses for the live stats box), so at the recipe's nominal condition
the charts reproduce its stats exactly; only the *change* is modelled:

  OG   gravity points scale with efficiency and inversely with final volume
  SRM  Morey: SRM = 1.4922 * MCU^0.6859, and MCU scales inversely with volume
  IBU  Tinseth: scales inversely with volume, times the bigness-factor ratio at the new OG
  FG   1 + (OG - 1) * (1 - attenuation)            (same formula as recipe_calculations.js)
  ABV  (OG - FG) * 131.25                          (same formula as recipe_calculations.js)
"""
MOREY_EXPONENT = 0.6859
POINTS = 41

STATS = ['OG', 'FG', 'IBU', 'SRM', 'ABV']


def _bigness(og):
    return 1.65 * 0.000125 ** (og - 1)


def _sweep(lo, hi, digits):
    step = (hi - lo) / (POINTS - 1)
    return [round(lo + i * step, digits) for i in range(POINTS)]


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def condition_charts(recipe):
    """Chart data for the viewer: per condition, an x sweep and each stat's predicted line,
    plus the yeast-attenuation band for FG/ABV and the style min/max for every stat."""
    points = sum(_num(f.get('PotentialGravity'), 0) for f in recipe.get('Fermentables') or [])
    if points <= 0:
        points = (_num(recipe.get('OG'), 1.0) - 1) * 1000
    ibu0 = sum(_num(h.get('IBU'), 0) for key in ('Hops', 'WhirlpoolHops', 'DryHops')
               for h in recipe.get(key) or [])
    if ibu0 <= 0:
        ibu0 = _num(recipe.get('ibu'), 0)
    srm0 = sum(_num(f.get('ColorPts'), 0) for f in recipe.get('Fermentables') or [])
    if srm0 <= 0:
        srm0 = _num(recipe.get('SRM'), 0)

    og0 = 1 + points / 1000
    eff0 = _num(recipe.get('Efficiency'))
    vol0 = _num(recipe.get('BatchSize'))
    yeast = recipe.get('Yeast') or {}
    att0 = _num(yeast.get('ExpectedAtten'), 75.0)
    att_min = _num(yeast.get('MinAtten'), att0 - 5)
    att_max = _num(yeast.get('MaxAtten'), att0 + 5)

    def predict(eff_ratio, vol_ratio, att):
        og = 1 + points * eff_ratio * vol_ratio / 1000
        fg = 1 + (og - 1) * (1 - att / 100)
        return {
            'OG': og,
            'FG': fg,
            'IBU': ibu0 * vol_ratio * (_bigness(og) / _bigness(og0)),
            'SRM': srm0 * vol_ratio ** MOREY_EXPONENT,
            'ABV': (og - fg) * 131.25,
        }

    conditions = []
    if eff0 and eff0 > 0:
        conditions.append({
            'key': 'efficiency', 'label': 'Brewhouse efficiency', 'unit': '%', 'nominal': eff0,
            'x': _sweep(max(30.0, eff0 - 20), min(95.0, eff0 + 20), 1),
            'at': lambda x: (x / eff0, 1.0),
        })
    if vol0 and vol0 > 0:
        conditions.append({
            'key': 'volume', 'label': 'Final batch volume', 'unit': 'gal', 'nominal': vol0,
            'x': _sweep(vol0 * 0.6, vol0 * 1.4, 3),
            'at': lambda x: (1.0, vol0 / x),
        })
    conditions.append({
        'key': 'attenuation', 'label': 'Yeast attenuation', 'unit': '%', 'nominal': att0,
        'x': _sweep(max(50.0, min(att_min, att0) - 10), min(98.0, max(att_max, att0) + 10), 1),
        'at': None,
    })

    out = []
    for c in conditions:
        series = {s: {'value': [], 'low': [], 'high': []} for s in STATS}
        for x in c['x']:
            if c['key'] == 'attenuation':
                mid, lo, hi = predict(1.0, 1.0, x), None, None
            else:
                eff_ratio, vol_ratio = c['at'](x)
                mid = predict(eff_ratio, vol_ratio, att0)
                lo = predict(eff_ratio, vol_ratio, att_min)
                hi = predict(eff_ratio, vol_ratio, att_max)
            for s in STATS:
                series[s]['value'].append(round(mid[s], 4))
                if lo and s in ('FG', 'ABV'):
                    pair = sorted([lo[s], hi[s]])
                    series[s]['low'].append(round(pair[0], 4))
                    series[s]['high'].append(round(pair[1], 4))
        for s in STATS:
            if not series[s]['low']:
                series[s]['low'] = series[s]['high'] = None
        out.append({key: c[key] for key in ('key', 'label', 'unit', 'nominal', 'x')} | {'series': series})

    # picobrew's stored FG/ABV come from its own (slightly different) formulas; rescale so the
    # recipe's nominal point equals the stats box while keeping the modelled relative change.
    nominal = predict(1.0, 1.0, att0)
    anchors = {
        'FG': (_num(recipe.get('FG')), lambda v, k: 1 + (v - 1) * k, lambda stored, model: (stored - 1) / (model - 1)),
        'ABV': (_num(recipe.get('abv')), lambda v, k: v * k, lambda stored, model: stored / model),
    }
    for stat, (stored, apply, ratio) in anchors.items():
        model = nominal[stat]
        if not stored or model in (0, 1) or (stat == 'FG' and stored <= 1):
            continue
        k = ratio(stored, model)
        for c in out:
            s = c['series'][stat]
            for key in ('value', 'low', 'high'):
                if s[key]:
                    s[key] = [round(apply(v, k), 4) for v in s[key]]
        nominal[stat] = apply(model, k)

    style = recipe.get('BeerStyle') or {}
    return {
        'conditions': out,
        'nominal': {s: round(nominal[s], 4) for s in STATS},
        'style': {s: [_num(style.get(f'Min{s}')), _num(style.get(f'Max{s}'))] for s in STATS},
        'attenuation_range': [att_min, att_max],
        'style_name': style.get('StyleNameCode'),
    }
