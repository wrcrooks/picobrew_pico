function showAlert(msg, type) {
    $('#alert').html("<div class='w-100 alert text-center alert-" + type + "'>" + msg + "</div>");
    $('#alert').show();
}

function formatTimestamp(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function scanStatusBadge(scan) {
    if (scan.found) return '<span class="badge badge-success">Recipe Found</span>';
    if (scan.event && scan.event.indexOf('error') === 0) return '<span class="badge badge-danger">Error</span>';
    return '<span class="badge badge-warning">Unknown Tag</span>';
}

// A physical tag scan always resolves to a legacy classic-format recipe (only the original
// Pico uses physical NFC/RFID PicoPak tags -- Zymatic/Z-Series pick recipes by GUID from an
// app), so it always lives on /pico_recipes. Name/ID search also covers the newer unified
// recipe store (/recipes, edited at /recipe/edit/<id>) since that's where recipe creation is
// consolidating -- 'source' says which page a given match lives on.
function recipeLink(source, id, label) {
    var href = source === 'unified' ? '/recipe/edit/' + encodeURIComponent(id) : '/pico_recipes#h_' + encodeURIComponent(id);
    return '<a href="' + href + '">' + escapeHtml(label) + '</a>';
}

function sourceBadge(source) {
    return source === 'unified'
        ? '<span class="badge badge-info ml-1">Unified</span>'
        : '<span class="badge badge-dark border ml-1">Legacy Pico</span>';
}

function renderScanRow(scan) {
    var recipeCell = scan.found
        ? recipeLink('pico', scan.rfid, scan.recipeName)
        : (scan.recipeName ? escapeHtml(scan.recipeName) : '<span class="text-muted">--</span>');
    return '<tr>' +
        '<td>' + formatTimestamp(scan.timestamp) + '</td>' +
        '<td><code>' + escapeHtml(scan.uid) + '</code></td>' +
        '<td><code>' + escapeHtml(scan.rfid) + '</code></td>' +
        '<td>' + recipeCell + '</td>' +
        '<td>' + scanStatusBadge(scan) + '</td>' +
        '</tr>';
}

function emptyScanRow() {
    return '<tr id="scan-log-empty"><td colspan="5" class="text-center text-muted">' +
        'No tag scans recorded yet. Scan a PicoPak on your Pico, or start a brew, to see it appear here.' +
        '</td></tr>';
}

function prependScanRow(scan) {
    var tbody = document.getElementById('scan-log-body');
    var emptyRow = document.getElementById('scan-log-empty');
    if (emptyRow) emptyRow.remove();
    tbody.insertAdjacentHTML('afterbegin', renderScanRow(scan));
}

function loadRecentScans() {
    fetch('/API/scanner/recent')
        .then(function (r) { return r.json(); })
        .then(function (scans) {
            var tbody = document.getElementById('scan-log-body');
            tbody.innerHTML = scans.length ? scans.map(renderScanRow).join('') : emptyScanRow();
        })
        .catch(function (err) {
            showAlert('Failed to load recent tag scans: ' + err, 'danger');
        });
}

function lookupTag() {
    var rfid = document.getElementById('lookup-rfid').value.trim();
    var resultDiv = document.getElementById('lookup-result');
    resultDiv.innerHTML = '';
    if (!rfid) return;

    fetch('/API/scanner/lookup?rfid=' + encodeURIComponent(rfid))
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.found) {
                var r = data.recipe;
                resultDiv.innerHTML =
                    '<div class="alert alert-success">' +
                    '<strong>' + escapeHtml(r.name) + '</strong>' +
                    sourceBadge(r.source) +
                    (r.is_archived ? ' <span class="badge badge-secondary ml-1">Archived</span>' : '') +
                    '<br>ABV: ' + r.abv + '%, IBU: ' + r.ibu +
                    '<br>' + recipeLink(r.source, r.id, 'View Recipe') +
                    '</div>';
            } else if (/^[0-9a-fA-F]{32}$/.test(rfid)) {
                resultDiv.innerHTML = '<div class="alert alert-warning">That looks like a recipe ID (32 characters), ' +
                    'not a physical tag ID -- try the recipe search below instead, or paste the value ' +
                    'from a recipe\'s Tag Programming section.</div>';
            } else {
                resultDiv.innerHTML = '<div class="alert alert-warning">No local recipe uses tag <code>' +
                    escapeHtml(data.rfid) + '</code>. It may be blank, from another server, or not yet synced.</div>';
            }
        })
        .catch(function (err) {
            resultDiv.innerHTML = '<div class="alert alert-danger">Lookup failed: ' + err + '</div>';
        });
}

function renderRecipeSearchResults(recipes, query) {
    var resultDiv = document.getElementById('search-recipe-result');
    if (!recipes.length) {
        resultDiv.innerHTML = '<div class="alert alert-warning">No recipes match <code>' +
            escapeHtml(query) + '</code>.</div>';
        return;
    }
    var rows = recipes.map(function (r) {
        var idCell = r.tagId
            ? '<code>' + escapeHtml(r.tagId) + '</code>'
            : '<span class="text-muted small">no tag assigned yet</span>';
        return '<tr>' +
            '<td>' + escapeHtml(r.name) + sourceBadge(r.source) +
            (r.is_archived ? ' <span class="badge badge-secondary ml-1">Archived</span>' : '') + '</td>' +
            '<td>' + idCell + '</td>' +
            '<td>' + r.abv + '%</td>' +
            '<td>' + r.ibu + '</td>' +
            '<td>' + recipeLink(r.source, r.id, 'View') + '</td>' +
            '</tr>';
    }).join('');
    resultDiv.innerHTML =
        '<div class="table-responsive"><table class="table table-dark table-striped table-bordered">' +
        '<thead><tr><th>Recipe</th><th>Tag ID</th><th>ABV</th><th>IBU</th><th></th></tr></thead>' +
        '<tbody>' + rows + '</tbody></table></div>';
}

function searchRecipesByName() {
    var query = document.getElementById('search-recipe-name').value.trim();
    var resultDiv = document.getElementById('search-recipe-result');
    resultDiv.innerHTML = '';
    if (!query) return;

    fetch('/API/scanner/search_recipe?q=' + encodeURIComponent(query))
        .then(function (r) { return r.json(); })
        .then(function (recipes) { renderRecipeSearchResults(recipes, query); })
        .catch(function (err) {
            resultDiv.innerHTML = '<div class="alert alert-danger">Search failed: ' + err + '</div>';
        });
}

function debounce(fn, delayMs) {
    var timer = null;
    return function () {
        clearTimeout(timer);
        timer = setTimeout(fn, delayMs);
    };
}

document.addEventListener('DOMContentLoaded', function () {
    loadRecentScans();

    document.getElementById('btn-lookup-tag').addEventListener('click', lookupTag);
    document.getElementById('lookup-rfid').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            lookupTag();
        }
    });

    document.getElementById('search-recipe-name').addEventListener('input', debounce(searchRecipesByName, 250));

    if (typeof socket !== 'undefined') {
        socket.on('tag_scanned', function (event) {
            prependScanRow(JSON.parse(event));
        });
    }
});
