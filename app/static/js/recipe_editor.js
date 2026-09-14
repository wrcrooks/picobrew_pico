// Generic add/remove/reorder support for the editable tables on the recipe editor page.
// Each managed <table> carries data-recipe-table="<TableName>" matching the dotted form
// field prefix used by its rows (e.g. name="Fermentables.0.Amount"). A <template
// id="row-template-<TableName>"> provides the blank row cloned by "Add Row". Row field
// names use a placeholder index until submit, when renumberRecipeTable() rewrites every
// row's fields to sequential indices 0..N-1 based on current DOM order -- this is what
// makes add/remove/reorder work without the indices ever getting out of sync.

function wireRecipeRowButtons(row) {
    var upBtn = row.querySelector('.recipe-row-up');
    var downBtn = row.querySelector('.recipe-row-down');
    var removeBtn = row.querySelector('.recipe-row-remove');

    if (upBtn) {
        upBtn.addEventListener('click', function () {
            var prev = row.previousElementSibling;
            if (prev) row.parentElement.insertBefore(row, prev);
        });
    }
    if (downBtn) {
        downBtn.addEventListener('click', function () {
            // the "+ Add Row" row is a plain <tr> with no data-recipe-row -- never swap past it
            var next = row.nextElementSibling;
            if (next && next.hasAttribute('data-recipe-row')) row.parentElement.insertBefore(next, row);
        });
    }
    if (removeBtn) {
        removeBtn.addEventListener('click', function () {
            row.remove();
        });
    }
}

function addRecipeRow(tableName) {
    var template = document.getElementById('row-template-' + tableName);
    var table = document.getElementById('table-' + tableName);
    if (!template || !table) return;
    var tbody = table.querySelector('tbody');
    var newRow = template.content.firstElementChild.cloneNode(true);
    wireRecipeRowButtons(newRow);
    // the "+ Add Row" row is always tbody's last child; insert new rows above it
    tbody.insertBefore(newRow, tbody.lastElementChild);
}

function renumberRecipeTable(table) {
    var tableName = table.dataset.recipeTable;
    var prefix = tableName + '.';
    var rows = table.querySelectorAll('tbody tr[data-recipe-row]');
    rows.forEach(function (row, index) {
        row.querySelectorAll('[name^="' + prefix + '"]').forEach(function (el) {
            var parts = el.name.split('.');
            parts[1] = index;
            el.name = parts.join('.');
        });
    });
}

document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-add-row-for]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            addRecipeRow(btn.dataset.addRowFor);
        });
    });

    document.querySelectorAll('table[data-recipe-table]').forEach(function (table) {
        table.querySelectorAll('tbody tr[data-recipe-row]').forEach(wireRecipeRowButtons);
    });

    var form = document.querySelector('form');
    if (form) {
        form.addEventListener('submit', function () {
            document.querySelectorAll('table[data-recipe-table]').forEach(renumberRecipeTable);
        });
    }
});
