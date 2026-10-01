// Ingredient autocomplete + autofill for the recipe editor, backed by picobrew.com's own
// ingredient database (app/static/data/picobrew_ingredients.json -- extracted directly from
// the live Crafter page's in-memory ingredient lists, not reconstructed/guessed).
//
// Autofill is exact for Hop AA% and Yeast fields (plain database lookups, no batch-dependent
// math involved). Fermentable Gravity/Color points are a best-effort estimate only: comparing
// canonical Yield/Color values against ~200 real recipes showed 5-20% typical scatter even
// with exact ingredient data, so there's no reliable closed-form formula to fall back on --
// these constants reproduce the most common real recipe configuration (2.5 gal batch, 55%
// efficiency) and are meant as a better-than-zero starting point, not an authoritative value.
const DEFAULT_GRAVITY_CONSTANT = 0.2068;
const DEFAULT_COLOR_CONSTANT = 0.242;

let PICOBREW_INGREDIENTS = null;

function findIngredientByName(list, name) {
    if (!list || !name) return null;
    const norm = name.trim().toLowerCase();
    return list.find(i => i.Name.trim().toLowerCase() === norm) || null;
}

function populateDatalist(id, items) {
    const dl = document.getElementById(id);
    if (!dl || !items) return;
    dl.innerHTML = items.map(i => `<option value="${i.Name.replace(/"/g, '&quot;')}">`).join('');
}

// Item 4 support: these return true/false (matched or not) so recalculateAllIngredients()
// can report how many rows it actually touched vs. left alone for lack of a database match.
function applyFermentableAutofill(row) {
    const nameInput = row.querySelector('[name$=".Name"]');
    const amountInput = row.querySelector('[name$=".Amount"]');
    const gravityInput = row.querySelector('[name$=".PotentialGravity"]');
    const colorInput = row.querySelector('[name$=".ColorPts"]');
    if (!nameInput) return false;
    const match = findIngredientByName(PICOBREW_INGREDIENTS.fermentables, nameInput.value);
    const amount = parseFloat(amountInput && amountInput.value) || 0;
    if (!match || amount <= 0) return false;
    if (gravityInput) gravityInput.value = (amount * match.Yield * DEFAULT_GRAVITY_CONSTANT).toFixed(2);
    if (colorInput) colorInput.value = (amount * match.Color * DEFAULT_COLOR_CONSTANT).toFixed(1);
    return true;
}

function applyHopAutofill(row) {
    const nameInput = row.querySelector('[name$=".Name"]');
    const amountInput = row.querySelector('[name$=".Amount"]');
    const alphaInput = row.querySelector('[name$=".Alpha"]');
    const timeInput = row.querySelector('[name$=".Time"]');
    const ibuInput = row.querySelector('[name$=".IBU"]');
    if (!nameInput) return false;
    const match = findIngredientByName(PICOBREW_INGREDIENTS.hops, nameInput.value);
    if (!match) return false;
    if (alphaInput) alphaInput.value = match.Alpha.toFixed(1);
    const amount = parseFloat(amountInput && amountInput.value) || 0;
    const time = parseFloat(timeInput && timeInput.value) || 0;
    if (ibuInput && amount > 0 && window.tinsethIBU) {
        const ibu = window.tinsethIBU(amount, match.Alpha, time, window.getCurrentOG(), window.getCurrentBatchSize());
        ibuInput.value = ibu.toFixed(1);
    }
    return true;
}

function wireFermentableAutofill(row) {
    const nameInput = row.querySelector('[name$=".Name"]');
    const amountInput = row.querySelector('[name$=".Amount"]');
    if (!nameInput) return;
    function autofill() {
        if (applyFermentableAutofill(row) && window.recomputeRecipeStats) window.recomputeRecipeStats();
    }
    nameInput.addEventListener('change', autofill);
    if (amountInput) amountInput.addEventListener('change', autofill);
}

function wireHopAutofill(row) {
    const nameInput = row.querySelector('[name$=".Name"]');
    const amountInput = row.querySelector('[name$=".Amount"]');
    const timeInput = row.querySelector('[name$=".Time"]');
    if (!nameInput) return;
    function autofill() {
        if (applyHopAutofill(row) && window.recomputeRecipeStats) window.recomputeRecipeStats();
    }
    nameInput.addEventListener('change', autofill);
    if (amountInput) amountInput.addEventListener('change', autofill);
    if (timeInput) timeInput.addEventListener('change', autofill);
}

// Item 5: nearest-match substitute suggestions, purely from the fields already on the row
// (not requiring the current Name to match the database) -- hops rank by |Alpha diff|,
// fermentables by a normalized Color+Yield distance. Injected via JS rather than touching
// every table's template/row-actions markup.
function suggestHopSubstitutes(row, n) {
    const alpha = parseFloat((row.querySelector('[name$=".Alpha"]') || {}).value);
    if (isNaN(alpha)) return [];
    const currentName = (row.querySelector('[name$=".Name"]') || {}).value || '';
    return PICOBREW_INGREDIENTS.hops
        .filter(h => h.Name.toLowerCase() !== currentName.trim().toLowerCase())
        .map(h => ({ item: h, distance: Math.abs(h.Alpha - alpha) }))
        .sort((a, b) => a.distance - b.distance)
        .slice(0, n)
        .map(x => `${x.item.Name} (${x.item.Alpha.toFixed(1)}% AA)`);
}

function suggestFermentableSubstitutes(row, n) {
    const color = parseFloat((row.querySelector('[name$=".ColorPts"]') || {}).value);
    const currentName = (row.querySelector('[name$=".Name"]') || {}).value || '';
    const match = findIngredientByName(PICOBREW_INGREDIENTS.fermentables, currentName);
    // Prefer the catalog's true Color/Yield for the current ingredient when it's a known
    // name (ColorPts on the row is a batch contribution, not the raw °L); fall back to
    // treating the row's ColorPts as a rough proxy for a completely custom entry.
    const refColor = match ? match.Color : color;
    const refYield = match ? match.Yield : 37;
    if (isNaN(refColor)) return [];
    return PICOBREW_INGREDIENTS.fermentables
        .filter(f => f.Name.toLowerCase() !== currentName.trim().toLowerCase())
        .map(f => ({
            item: f,
            distance: Math.abs(f.Color - refColor) / Math.max(refColor, 5) +
                      Math.abs(f.Yield - refYield) / Math.max(refYield, 20)
        }))
        .sort((a, b) => a.distance - b.distance)
        .slice(0, n)
        .map(x => `${x.item.Name} (${x.item.Color}°L, ${x.item.Yield} PPG)`);
}

function toggleSubstituteSuggestions(row, category) {
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains('substitute-row')) {
        existing.remove();
        return;
    }
    row.parentElement.querySelectorAll('.substitute-row').forEach(r => r.remove());

    const suggestions = category === 'hop' ? suggestHopSubstitutes(row, 5) : suggestFermentableSubstitutes(row, 5);
    const nameInput = row.querySelector('[name$=".Name"]');
    const tr = document.createElement('tr');
    tr.className = 'substitute-row';
    const td = document.createElement('td');
    td.colSpan = row.children.length;
    td.className = 'small text-muted';
    if (suggestions.length === 0) {
        td.textContent = 'No substitutes to suggest -- enter an Alpha%/Color first.';
    } else {
        td.textContent = 'Similar: ';
        suggestions.forEach((label, i) => {
            const link = document.createElement('a');
            link.href = '#';
            link.textContent = label;
            link.className = 'mr-2';
            link.addEventListener('click', (e) => {
                e.preventDefault();
                nameInput.value = label.replace(/\s*\(.*\)$/, '');
                nameInput.dispatchEvent(new Event('change', { bubbles: true }));
                tr.remove();
            });
            td.appendChild(link);
        });
    }
    tr.appendChild(td);
    row.parentElement.insertBefore(tr, row.nextSibling);
}

function addSubstituteButton(row, category) {
    const actionsCell = row.querySelector('td:last-child');
    if (!actionsCell || actionsCell.querySelector('.btn-suggest-sub')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-sm btn-outline-info btn-suggest-sub';
    btn.title = 'Suggest substitutes';
    btn.innerHTML = '<i class="fas fa-exchange-alt"></i>';
    btn.addEventListener('click', () => toggleSubstituteSuggestions(row, category));
    actionsCell.insertBefore(btn, actionsCell.firstChild);
}

// Item 4: bulk-reapplies the same autofill every row already gets individually on
// Name/Amount change -- lets a legacy/imported recipe's stale per-row points fields
// (an issue I found repeatedly reverse-engineering picobrew's own recipe corpus) be
// refreshed in one click instead of nudging each row to re-trigger its listener.
function recalculateAllIngredients() {
    let matched = 0, skipped = 0;
    document.querySelectorAll('#table-Fermentables tbody tr[data-recipe-row]').forEach(row => {
        (applyFermentableAutofill(row) ? matched++ : skipped++);
    });
    ['table-Hops', 'table-WhirlpoolHops', 'table-DryHops'].forEach(tableId => {
        const table = document.getElementById(tableId);
        if (!table) return;
        table.querySelectorAll('tbody tr[data-recipe-row]').forEach(row => {
            (applyHopAutofill(row) ? matched++ : skipped++);
        });
    });
    if (window.recomputeRecipeStats) window.recomputeRecipeStats();
    const statusEl = document.getElementById('recalculate-status');
    if (statusEl) {
        statusEl.textContent = `Updated ${matched} row${matched === 1 ? '' : 's'} from the ingredient database` +
            (skipped ? `; ${skipped} left unchanged (no name match)` : '.');
    }
}
window.recalculateAllIngredients = recalculateAllIngredients;

// Item 7 (second half): flag when the current yeast's typed ExpectedAtten strays outside
// that same named yeast's own catalog Min/MaxAtten -- reuses the same database lookup as
// autofill above, just checking rather than overwriting.
function checkYeastAttenuation() {
    const nameInput = document.querySelector('[name="Yeast.Name"]');
    const attenInput = document.querySelector('[name="Yeast.ExpectedAtten"]');
    const warningEl = document.getElementById('stat-atten-warning');
    if (!nameInput || !attenInput || !warningEl) return;
    const match = findIngredientByName(PICOBREW_INGREDIENTS.yeasts, nameInput.value);
    const atten = parseFloat(attenInput.value);
    if (match && !isNaN(atten) && (atten < match.MinAtten || atten > match.MaxAtten)) {
        warningEl.textContent = `⚠ ${match.Name}'s usual range is ${match.MinAtten}-${match.MaxAtten}% attenuation`;
        warningEl.hidden = false;
    } else {
        warningEl.hidden = true;
    }
}

// Item 6: suggests yeasts/hops actually paired with the selected style across the user's
// own local recipe library (GET /api/style_suggestions, built server-side in
// routes_frontend.py's build_style_suggestions() from load_redux_recipes()). Empty lists
// mean no local data for that style -- rendered as nothing, never a fabricated guess.
function renderSuggestionChips(containerId, names, onPick) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    if (!names || names.length === 0) return;
    container.appendChild(document.createTextNode('Common for this style: '));
    names.forEach(name => {
        const link = document.createElement('a');
        link.href = '#';
        link.textContent = name;
        link.className = 'badge badge-secondary mr-1';
        link.addEventListener('click', (e) => { e.preventDefault(); onPick(name); });
        container.appendChild(link);
    });
}

function pickYeastSuggestion(name) {
    const nameInput = document.querySelector('[name="Yeast.Name"]');
    if (!nameInput) return;
    nameInput.value = name;
    nameInput.dispatchEvent(new Event('change', { bubbles: true }));
}

function pickHopSuggestion(name) {
    if (typeof addRecipeRow !== 'function') return;
    addRecipeRow('Hops');
    const rows = document.querySelectorAll('#table-Hops tbody tr[data-recipe-row]');
    const newRow = rows[rows.length - 1];
    const nameInput = newRow && newRow.querySelector('[name$=".Name"]');
    if (nameInput) {
        nameInput.value = name;
        nameInput.dispatchEvent(new Event('change', { bubbles: true }));
    }
}

async function updateStyleSuggestions() {
    const select = document.getElementById('recipe_StyleNameCode');
    if (!select || !select.value) return;
    let data;
    try {
        const resp = await fetch('/api/style_suggestions?style=' + encodeURIComponent(select.value));
        data = await resp.json();
    } catch (e) {
        data = { yeasts: [], hops: [] };
    }
    renderSuggestionChips('yeast-suggestions', data.yeasts, pickYeastSuggestion);
    renderSuggestionChips('hop-suggestions', data.hops, pickHopSuggestion);
}

function wireYeastAutofill() {
    const nameInput = document.querySelector('[name="Yeast.Name"]');
    const attenInput = document.querySelector('[name="Yeast.ExpectedAtten"]');
    if (!nameInput) return;
    nameInput.addEventListener('change', () => {
        const match = findIngredientByName(PICOBREW_INGREDIENTS.yeasts, nameInput.value);
        if (!match) return;
        const setVal = (sel, val) => { const el = document.querySelector(sel); if (el) el.value = val; };
        setVal('[name="Yeast.ExpectedAtten"]', match.ExpectedAtten);
        setVal('[name="Yeast.MinTemp"]', match.MinTemp);
        setVal('[name="Yeast.MaxTemp"]', match.MaxTemp);
        setVal('[name="Yeast.ExpectedTemp"]', match.ExpectedTemp);
        if (window.recomputeRecipeStats) window.recomputeRecipeStats();
        checkYeastAttenuation();
    });
    if (attenInput) attenInput.addEventListener('input', checkYeastAttenuation);
}

// Item 1: the catalog now spans 3 BJCP guide years per style name (2008/2015/2021), so a
// bare name match is ambiguous -- prefer whichever guide this recipe was already using
// (real recipes always carry BeerStyle.StyleGuide), falling back to the first match for a
// brand-new/blank recipe that has none yet.
function wireStyleRangeUpdate() {
    const select = document.getElementById('recipe_StyleNameCode');
    const guideInput = document.getElementById('recipe_StyleGuide');
    if (!select) return;
    select.addEventListener('change', () => {
        const currentGuide = guideInput ? guideInput.value : '';
        const candidates = PICOBREW_INGREDIENTS.beerStyles.filter(s => s.StyleNameCode === select.value);
        const match = candidates.find(s => s.StyleGuide === currentGuide) || candidates[0];
        if (!match) return;
        const setRange = (id, min, max, fmt) => {
            const el = document.getElementById(id);
            if (el) {
                el.textContent = `MIN: ${fmt(min)} MAX: ${fmt(max)}`;
                el.dataset.min = min;
                el.dataset.max = max;
            }
        };
        setRange('range-og', match.MinOG, match.MaxOG, v => v.toFixed(3));
        setRange('range-fg', match.MinFG, match.MaxFG, v => v.toFixed(3));
        setRange('range-ibu', match.MinIBU, match.MaxIBU, v => v.toFixed(0));
        setRange('range-srm', match.MinSRM, match.MaxSRM, v => v.toFixed(0));
        setRange('range-abv', match.MinABV, match.MaxABV, v => v.toFixed(1));
        if (window.recomputeRecipeStats) window.recomputeRecipeStats();
        updateStyleSuggestions();
    });
}

// Called for every row present on page load, and (via window.wireNewRecipeRow) for rows
// added afterward by recipe_editor.js's "Add Row" button.
function wireRowForTable(tableId, row) {
    if (tableId === 'Fermentables') {
        wireFermentableAutofill(row);
        addSubstituteButton(row, 'fermentable');
    }
    if (tableId === 'Hops' || tableId === 'WhirlpoolHops' || tableId === 'DryHops') {
        wireHopAutofill(row);
        addSubstituteButton(row, 'hop');
    }
}

window.wireNewRecipeRow = function (tableId, row) {
    if (!PICOBREW_INGREDIENTS) return;
    wireRowForTable(tableId, row);
};

async function loadIngredientDatabase() {
    try {
        const resp = await fetch('/static/data/picobrew_ingredients.json');
        PICOBREW_INGREDIENTS = await resp.json();
    } catch (e) {
        console.warn('Could not load picobrew ingredient database', e);
        return;
    }
    populateDatalist('datalist-fermentables', PICOBREW_INGREDIENTS.fermentables);
    populateDatalist('datalist-hops', PICOBREW_INGREDIENTS.hops);
    populateDatalist('datalist-yeasts', PICOBREW_INGREDIENTS.yeasts);
    populateDatalist('datalist-adjuncts', PICOBREW_INGREDIENTS.adjuncts);
    populateDatalist('datalist-amendments', PICOBREW_INGREDIENTS.amendments);

    document.querySelectorAll('table[data-recipe-table] tbody tr[data-recipe-row]').forEach(row => {
        const tableId = row.closest('table[data-recipe-table]').dataset.recipeTable;
        wireRowForTable(tableId, row);
    });
    wireYeastAutofill();
    wireStyleRangeUpdate();
    checkYeastAttenuation();
    updateStyleSuggestions();
    processPendingIngredients();
}

// Item 8: the other half of ingredients.html's "+ Add to Recipe" button -- picks queued in
// localStorage (never a server round-trip, so this recipe's own unsaved edits are untouched)
// get turned into a new row (or, for Yeast, a straight field-set) via the exact same
// addRecipeRow + autofill path every other row already goes through.
function processPendingIngredients() {
    const form = document.querySelector('form[data-recipe-id]');
    if (!form) return;
    const key = 'pending_ingredients_' + form.dataset.recipeId;
    let pending;
    try { pending = JSON.parse(localStorage.getItem(key) || '[]'); } catch (e) { pending = []; }
    localStorage.removeItem(key);
    if (!Array.isArray(pending) || pending.length === 0) return;

    const tableForCategory = { Fermentables: 'Fermentables', Hops: 'Hops', Adjuncts: 'Adjuncts', WaterAmendments: 'Amendments' };
    pending.forEach(item => {
        if (item.category === 'Yeast') {
            pickYeastSuggestion(item.name);
            return;
        }
        if (item.category === 'BeerStyles') {
            const select = document.getElementById('recipe_StyleNameCode');
            if (select && Array.from(select.options).some(o => o.value === item.name)) {
                select.value = item.name;
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }
            return;
        }
        const tableName = tableForCategory[item.category];
        if (!tableName || typeof addRecipeRow !== 'function') return;
        addRecipeRow(tableName);
        const rows = document.querySelectorAll(`#table-${tableName} tbody tr[data-recipe-row]`);
        const newRow = rows[rows.length - 1];
        const nameInput = newRow && newRow.querySelector('[name$=".Name"]');
        if (nameInput) {
            nameInput.value = item.name;
            nameInput.dispatchEvent(new Event('change', { bubbles: true }));
        }
    });
    if (window.recomputeRecipeStats) window.recomputeRecipeStats();
}

document.addEventListener('DOMContentLoaded', loadIngredientDatabase);

// Fires when the /ingredients tab (a separate tab -- see "Browse Ingredients" links) writes
// a pick to localStorage. Lets this tab pick it up immediately without ever reloading, so
// any of this recipe's own unsaved edits are completely undisturbed.
window.addEventListener('storage', (e) => {
    const form = document.querySelector('form[data-recipe-id]');
    if (!form || !e.key || e.key !== `pending_ingredients_${form.dataset.recipeId}`) return;
    if (PICOBREW_INGREDIENTS) processPendingIngredients();
});
