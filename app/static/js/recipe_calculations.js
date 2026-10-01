// Live-recalculates the OG/FG/IBU/SRM/ABV stats box on the recipe editor as ingredients are
// edited. Every formula here is derived from picobrew.com's own recipe data (each ingredient
// already carries the site's precomputed contribution -- Fermentables[].PotentialGravity/ColorPts,
// Hops[].IBU -- confirmed by summing them across dozens of real recipes and matching the stored
// OG/SRM/IBU exactly), not a reimplementation of picobrew's server-side formulas.
//
//   SRM = sum of Fermentables[].ColorPts
//   OG  = 1 + sum(Fermentables[].PotentialGravity) / 1000
//   IBU = sum of Hops/WhirlpoolHops/DryHops[].IBU
//   FG  = 1 + (OG - 1) * (1 - Yeast.ExpectedAtten / 100)   (standard apparent-attenuation formula)
//   ABV = (OG - FG) * 131.25                               (standard homebrew estimate)

// Standard Tinseth (1997) hop utilization formula, calibrated against 249 real hop additions
// across every local recipe (mean ratio 1.1021, stdev 0.0057 -- a much tighter fit than the
// fermentable gravity/color estimates below, so this one's on solid ground).
var TINSETH_CALIBRATION = 0.9074;

function tinsethIBU(amountOz, alphaPercent, timeMin, og, batchSizeGal) {
    if (!amountOz || !alphaPercent || !batchSizeGal || og <= 1) return 0;
    var bignessFactor = 1.65 * Math.pow(0.000125, og - 1);
    var boilTimeFactor = (1 - Math.exp(-0.04 * timeMin)) / 4.15;
    var utilization = bignessFactor * boilTimeFactor;
    return (amountOz * (alphaPercent / 100) * utilization * 7489 * TINSETH_CALIBRATION) / batchSizeGal;
}

function getCurrentOG() {
    return 1 + sumInputs('input[name$=".PotentialGravity"]') / 1000;
}

function getCurrentBatchSize() {
    var el = document.getElementById('input-batchsize');
    return el ? (parseFloat(el.value) || 0) : 0;
}

window.tinsethIBU = tinsethIBU;
window.getCurrentOG = getCurrentOG;
window.getCurrentBatchSize = getCurrentBatchSize;

function sumInputs(selector) {
    var total = 0;
    document.querySelectorAll(selector).forEach(function (el) {
        total += parseFloat(el.value) || 0;
    });
    return total;
}

function maxInputs(selectors) {
    var max = 0;
    selectors.forEach(function (selector) {
        document.querySelectorAll(selector).forEach(function (el) {
            var v = parseFloat(el.value) || 0;
            if (v > max) max = v;
        });
    });
    return max;
}

// Pre-hop Boil Time and Whirlpool Time aren't stored fields on picobrew.com either -- they're
// always derived (there and here) from Total Boil Time and whatever sat in the boil/whirlpool
// the longest. Mirrors estimate_recipe_brew_chill_time's siblings in routes_frontend.py.
function recomputeDerivedTimes() {
    var boilTimeInput = document.querySelector('input[name="BoilTime"]');
    var boilTime = boilTimeInput ? (parseFloat(boilTimeInput.value) || 0) : 0;
    var maxBoilStepTime = maxInputs(['input[name^="Hops."][name$=".Time"]', 'input[name^="Adjuncts."][name$=".Time"]']);
    var preHopEl = document.getElementById('pre-hop-boil-time');
    if (preHopEl) preHopEl.textContent = (boilTime - maxBoilStepTime).toFixed(1);

    var whirlpoolTime = maxInputs([
        'input[name^="WhirlpoolSteps."][name$=".Time"]',
        'input[name^="WhirlpoolHops."][name$=".Time"]',
        'input[name^="WhirlpoolAdjuncts."][name$=".Time"]'
    ]);
    var whirlpoolEl = document.getElementById('whirlpool-time');
    if (whirlpoolEl) whirlpoolEl.textContent = whirlpoolTime;

    var h2oInput = document.querySelector('input[name="H2O"]');
    var h2oLbsEl = document.getElementById('h2o-lbs');
    if (h2oInput && h2oLbsEl) {
        var gal = parseFloat(h2oInput.value) || 0;
        h2oLbsEl.textContent = '(' + (gal * 8.34).toFixed(2) + ' lbs)';
    }
}

// Item 1: color each stat green/red depending on whether it falls inside the selected
// style's Min/Max (read from the range div's data-min/data-max -- recipe_ingredients.js
// keeps these current when the Style dropdown changes).
function applyStyleCompliance(statId, rangeId, value) {
    var statEl = document.getElementById(statId);
    var rangeEl = document.getElementById(rangeId);
    if (!statEl || !rangeEl) return;
    var min = parseFloat(rangeEl.dataset.min);
    var max = parseFloat(rangeEl.dataset.max);
    statEl.classList.remove('text-success', 'text-danger');
    if (isNaN(min) || isNaN(max)) return;
    statEl.classList.add(value >= min && value <= max ? 'text-success' : 'text-danger');
}

// Item 7: BU:GU (bitterness-to-gravity ratio) is a standard, public homebrewing balance
// heuristic -- not picobrew-specific, no live-site verification needed.
function describeBalance(ibu, og) {
    if (og <= 1) return '--';
    var buGu = ibu / ((og - 1) * 1000);
    var label;
    if (buGu < 0.4) label = 'Malty';
    else if (buGu <= 0.8) label = 'Balanced';
    else label = 'Bitter';
    return label + ' (BU:GU ' + buGu.toFixed(2) + ')';
}

function recomputeRecipeStats() {
    recomputeDerivedTimes();
    var srm = sumInputs('input[name$=".ColorPts"]');
    var gravityPts = sumInputs('input[name$=".PotentialGravity"]');
    var ibu = sumInputs('input[name$=".IBU"]');

    var og = 1 + gravityPts / 1000;
    var attenInput = document.querySelector('input[name="Yeast.ExpectedAtten"]');
    var atten = attenInput ? (parseFloat(attenInput.value) || 0) : 0;
    var fg = 1 + (og - 1) * (1 - atten / 100);
    var abv = (og - fg) * 131.25;

    var ogEl = document.getElementById('stat-og');
    var fgEl = document.getElementById('stat-fg');
    var ibuEl = document.getElementById('stat-ibu');
    var srmEl = document.getElementById('stat-srm');
    var abvEl = document.getElementById('stat-abv');

    if (ogEl) ogEl.textContent = og.toFixed(3);
    if (fgEl) fgEl.textContent = fg.toFixed(3);
    if (ibuEl) ibuEl.textContent = ibu.toFixed(0);
    if (srmEl) srmEl.textContent = srm.toFixed(0);
    if (abvEl) abvEl.textContent = abv.toFixed(1) + '%';

    applyStyleCompliance('stat-og', 'range-og', og);
    applyStyleCompliance('stat-fg', 'range-fg', fg);
    applyStyleCompliance('stat-ibu', 'range-ibu', ibu);
    applyStyleCompliance('stat-srm', 'range-srm', srm);
    applyStyleCompliance('stat-abv', 'range-abv', abv);

    var balanceEl = document.getElementById('stat-balance');
    if (balanceEl) balanceEl.textContent = describeBalance(ibu, og);
}

window.recomputeRecipeStats = recomputeRecipeStats;

// Item 3: scaling every ingredient Amount and the batch volume (BatchSize/H2O) by the same
// ratio leaves concentration-based stats (OG/FG/SRM/IBU/ABV) unchanged, since each row's
// stored PotentialGravity/ColorPts/IBU already represents Amount/BatchSize -- so those points
// fields need no adjustment at all, only the raw weights and volumes do. The exception is a
// machine with hardware-fixed starting water (MACHINE_FIXED_WATER_GAL in model.py), whose
// water is set to that fixed amount instead of scaled.
function scaleRecipeTo(target) {
    var batchSizeInput = document.getElementById('input-batchsize');
    if (!batchSizeInput) return;
    var current = parseFloat(batchSizeInput.value) || 0;
    if (current <= 0 || target <= 0) return;
    var ratio = target / current;

    document.querySelectorAll('input[name$=".Amount"]').forEach(function (el) {
        el.value = ((parseFloat(el.value) || 0) * ratio).toFixed(2);
    });
    batchSizeInput.value = target.toFixed(2);
    var h2oInput = document.querySelector('input[name="H2O"]');
    if (h2oInput) {
        var machineSelect = document.getElementById('input-machine');
        var fixedWater = machineSelect && window.MACHINE_FIXED_WATER
            ? window.MACHINE_FIXED_WATER[machineSelect.value] : undefined;
        h2oInput.value = fixedWater !== undefined
            ? String(fixedWater)
            : ((parseFloat(h2oInput.value) || 0) * ratio).toFixed(2);
    }
    recomputeRecipeStats();
}
window.scaleRecipeTo = scaleRecipeTo;

function wireScaleRecipe() {
    var btn = document.getElementById('btn-scale-recipe');
    var targetInput = document.getElementById('input-scale-target');
    if (!btn || !targetInput) return;
    btn.addEventListener('click', function () {
        scaleRecipeTo(parseFloat(targetInput.value) || 0);
        targetInput.value = '';
    });
}

// Machine batch-size presets (Pico C/S, Zymatic, Z Series, Custom -- see
// MACHINE_BATCH_PRESETS in app/main/model.py for where these numbers come from). Picking a
// machine shows one button per its standard batch size; clicking one scales the recipe to
// it immediately via the same math as the manual "Scale Recipe To" control above.
function renderMachinePresets() {
    var select = document.getElementById('input-machine');
    var container = document.getElementById('machine-batch-presets');
    if (!select || !container || !window.MACHINE_BATCH_PRESETS) return;
    var presets = window.MACHINE_BATCH_PRESETS[select.value] || [];
    container.innerHTML = '';
    if (presets.length === 0) return;
    var label = document.createElement('span');
    label.className = 'text-muted small mr-2';
    label.textContent = 'Scale to:';
    container.appendChild(label);
    presets.forEach(function (preset) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-outline-secondary mr-1 mb-1';
        btn.textContent = preset.label;
        btn.addEventListener('click', function () { scaleRecipeTo(preset.value); });
        container.appendChild(btn);
    });
}

function wireMachineSelect() {
    var select = document.getElementById('input-machine');
    if (!select) return;
    select.addEventListener('change', renderMachinePresets);
    renderMachinePresets();
}

document.addEventListener('DOMContentLoaded', function () {
    recomputeRecipeStats();
    wireScaleRecipe();
    wireMachineSelect();

    var form = document.querySelector('form');
    if (form) {
        form.addEventListener('input', recomputeRecipeStats);
    }
});
