// Small-multiple sensitivity charts on the recipe viewer: how OG/FG/IBU/SRM/ABV move as one
// brewing condition varies. Data comes from app/main/recipe_conditions.py via #condition-data.
(function () {
    var dataEl = document.getElementById('condition-data');
    var root = document.getElementById('cond-root');
    if (!dataEl || !root || typeof Chart === 'undefined') return;

    var DATA = JSON.parse(dataEl.textContent);
    var STATS = ['OG', 'FG', 'IBU', 'SRM', 'ABV'];
    var BANDED = { FG: true, ABV: true };
    var FMT = {
        OG: function (v) { return v.toFixed(3); },
        FG: function (v) { return v.toFixed(3); },
        IBU: function (v) { return v.toFixed(0); },
        SRM: function (v) { return v.toFixed(0); },
        ABV: function (v) { return v.toFixed(1) + '%'; },
    };
    var DESCRIBE = {
        efficiency: function (c) {
            return 'If your system extracts more or less sugar than this recipe\'s ' + fmtX(c, c.nominal) +
                ' brewhouse efficiency. SRM is unaffected.';
        },
        volume: function (c) {
            return 'If you end with more or less wort than this recipe\'s ' + fmtX(c, c.nominal) +
                ' (e.g. from boil-off or grain absorption). Less wort concentrates gravity, bitterness and color.';
        },
        attenuation: function (c) {
            return 'If the yeast ferments more or less completely than its expected ' + fmtX(c, c.nominal) +
                ' attenuation. Only FG and ABV change.';
        },
    };

    var css = getComputedStyle(root);
    function token(name) { return css.getPropertyValue(name).trim(); }
    var C = {
        series: token('--cond-series'),
        seriesWash: token('--cond-series-wash'),
        style: token('--cond-style'),
        styleWash: token('--cond-style-wash'),
        surface: token('--cond-surface'),
        grid: token('--cond-grid'),
        axis: token('--cond-axis'),
        muted: token('--cond-muted'),
        text: token('--cond-text'),
    };
    var FONT = { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 11 };

    function fmtX(c, x) {
        return c.unit === '%' ? x.toFixed(0) + '%' : (+x.toFixed(2)) + ' ' + c.unit;
    }

    function verdict(stat, v) {
        var range = DATA.style[stat];
        if (range[1] != null && v > range[1]) return 'above style';
        if (range[0] != null && v < range[0]) return 'below style';
        if (range[0] != null || range[1] != null) return 'in style';
        return '';
    }

    function nearestIndex(xs, x) {
        var best = 0;
        xs.forEach(function (v, i) { if (Math.abs(v - x) < Math.abs(xs[best] - x)) best = i; });
        return best;
    }

    // Vertical hairline at the hovered X, so readers aim at a condition, not a 2px line.
    var crosshair = {
        id: 'condCrosshair',
        afterDatasetsDraw: function (chart) {
            var active = chart.tooltip && chart.tooltip.getActiveElements();
            if (!active || !active.length) return;
            var x = active[0].element.x, area = chart.chartArea, ctx = chart.ctx;
            ctx.save();
            ctx.strokeStyle = C.axis;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x, area.top);
            ctx.lineTo(x, area.bottom);
            ctx.stroke();
            ctx.restore();
        },
    };

    // Sparing direct labels: the style limits at the right edge, and the recipe's own value.
    var directLabels = {
        id: 'condLabels',
        afterDatasetsDraw: function (chart, args, meta) {
            if (!meta || !meta.cond) return;
            var ctx = chart.ctx, area = chart.chartArea, y = chart.scales.y, x = chart.scales.x;
            var values = meta.cond.series[meta.stat].value;
            ctx.save();
            ctx.font = '11px ' + FONT.family;
            ctx.lineJoin = 'round';
            // Each style label goes on the edge where the line is farther away, with a surface
            // halo so a crossing line never makes it illegible.
            function styleLabel(text, value, below) {
                var py = y.getPixelForValue(value);
                var leftGap = Math.abs(y.getPixelForValue(values[0]) - py);
                var rightGap = Math.abs(y.getPixelForValue(values[values.length - 1]) - py);
                var left = leftGap > rightGap;
                ctx.textAlign = left ? 'left' : 'right';
                ctx.textBaseline = below ? 'top' : 'bottom';
                var tx = left ? area.left + 4 : area.right - 4, ty = below ? py + 2 : py - 2;
                ctx.strokeStyle = C.surface;
                ctx.lineWidth = 3;
                ctx.strokeText(text, tx, ty);
                ctx.fillStyle = C.muted;
                ctx.fillText(text, tx, ty);
            }
            var range = DATA.style[meta.stat];
            if (range[1] != null) styleLabel('style max ' + FMT[meta.stat](range[1]), range[1], false);
            if (range[0] != null) styleLabel('style min ' + FMT[meta.stat](range[0]), range[0], true);
            var px = x.getPixelForValue(meta.cond.nominal), py = y.getPixelForValue(meta.nominal);
            ctx.fillStyle = C.text;
            ctx.textAlign = px > area.left + (area.right - area.left) * 0.75 ? 'right' : 'left';
            ctx.textBaseline = 'bottom';
            ctx.font = '600 11px ' + FONT.family;
            var label = FMT[meta.stat](meta.nominal), lx = px + (ctx.textAlign === 'left' ? 7 : -7);
            ctx.strokeStyle = C.surface;
            ctx.lineWidth = 3;
            ctx.strokeText(label, lx, py - 6);
            ctx.fillText(label, lx, py - 6);
            ctx.restore();
        },
    };

    function points(xs, ys) { return xs.map(function (x, i) { return { x: x, y: ys[i] }; }); }

    function constant(xs, value) { return xs.map(function (x) { return { x: x, y: value }; }); }

    function datasets(stat, cond) {
        var s = cond.series[stat], xs = cond.x, range = DATA.style[stat], sets = [];
        var flat = { pointRadius: 0, pointHoverRadius: 0, tension: 0 };
        if (range[0] != null && range[1] != null) {
            sets.push(Object.assign({ role: 'styleMin', data: constant(xs, range[0]), borderColor: C.style, borderWidth: 1, fill: false }, flat));
            sets.push(Object.assign({ role: 'styleMax', data: constant(xs, range[1]), borderColor: C.style, borderWidth: 1, fill: '-1', backgroundColor: C.styleWash }, flat));
        } else if (range[0] != null || range[1] != null) {
            sets.push(Object.assign({ role: 'styleEdge', data: constant(xs, range[0] != null ? range[0] : range[1]), borderColor: C.style, borderWidth: 1, fill: false }, flat));
        }
        if (s.low) {
            sets.push(Object.assign({ role: 'low', data: points(xs, s.low), borderWidth: 0, fill: false }, flat));
            sets.push(Object.assign({ role: 'high', data: points(xs, s.high), borderWidth: 0, fill: '-1', backgroundColor: C.seriesWash }, flat));
        }
        sets.push({
            role: 'value', data: points(xs, s.value), borderColor: C.series, borderWidth: 2,
            borderCapStyle: 'round', borderJoinStyle: 'round', fill: false, tension: 0,
            pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: C.series,
            pointHoverBorderColor: C.surface, pointHoverBorderWidth: 2,
        });
        sets.push({
            role: 'nominal', data: [{ x: cond.nominal, y: DATA.nominal[stat] }], showLine: false,
            pointRadius: 5, pointHoverRadius: 5, pointBackgroundColor: C.series,
            pointBorderColor: C.surface, pointBorderWidth: 2,
        });
        return sets;
    }

    function yBounds(stat, cond) {
        var s = cond.series[stat], vals = s.value.slice();
        if (s.low) vals = vals.concat(s.low, s.high);
        DATA.style[stat].forEach(function (v) { if (v != null) vals.push(v); });
        var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
        var pad = (hi - lo || Math.abs(hi) * 0.1 || 1) * 0.15;
        return { min: lo - pad, max: hi + pad };
    }

    function options(stat, cond) {
        var b = yBounds(stat, cond);
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', axis: 'x', intersect: false },
            layout: { padding: { top: 8, right: 4 } },
            scales: {
                x: {
                    type: 'linear', min: cond.x[0], max: cond.x[cond.x.length - 1],
                    title: { display: true, text: cond.label + ' (' + cond.unit + ')', color: C.muted, font: FONT },
                    ticks: { color: C.muted, font: FONT, maxTicksLimit: 6, includeBounds: false, callback: function (v) { return +v.toFixed(2); } },
                    grid: { color: C.grid, lineWidth: 1, drawTicks: false },
                    border: { color: C.axis },
                },
                y: {
                    suggestedMin: b.min, suggestedMax: b.max,
                    ticks: { color: C.muted, font: FONT, maxTicksLimit: 5, callback: function (v) { return FMT[stat](v); } },
                    grid: { color: C.grid, lineWidth: 1, drawTicks: false },
                    border: { color: C.axis },
                },
            },
            plugins: {
                legend: { display: false },
                datalabels: { display: false },
                condLabels: { stat: stat, cond: cond, nominal: DATA.nominal[stat] },
                tooltip: {
                    usePointStyle: true,
                    filter: function (item) { return item.dataset.role === 'value'; },
                    callbacks: {
                        title: function (items) { return cond.label + ': ' + fmtX(cond, items[0].parsed.x); },
                        label: function (item) { return stat + ' ' + FMT[stat](item.parsed.y); },
                        labelPointStyle: function () { return { pointStyle: 'line', rotation: 0 }; },
                        footer: function (items) {
                            var i = items[0].dataIndex, s = cond.series[stat], lines = [];
                            if (s.low) lines.push('Yeast range ' + FMT[stat](s.low[i]) + ' – ' + FMT[stat](s.high[i]));
                            var range = DATA.style[stat];
                            if (range[0] != null || range[1] != null) {
                                lines.push('Style ' + (range[0] != null ? FMT[stat](range[0]) : '–') + ' – ' +
                                    (range[1] != null ? FMT[stat](range[1]) : '–') + ' · ' + verdict(stat, s.value[i]));
                            }
                            return lines;
                        },
                    },
                },
            },
        };
    }

    var charts = {};
    function render(condKey) {
        var cond = DATA.conditions.filter(function (c) { return c.key === condKey; })[0];
        root.querySelectorAll('[data-cond]').forEach(function (btn) {
            var on = btn.dataset.cond === condKey;
            btn.classList.toggle('active', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        document.getElementById('cond-description').textContent = DESCRIBE[cond.key](cond);
        document.getElementById('cond-key-yeast').hidden = cond.key === 'attenuation';

        STATS.forEach(function (stat) {
            var canvas = document.getElementById('cond-chart-' + stat);
            if (charts[stat]) charts[stat].destroy();
            charts[stat] = new Chart(canvas, { type: 'line', data: { datasets: datasets(stat, cond) },
                options: options(stat, cond), plugins: [crosshair, directLabels] });
            canvas.setAttribute('aria-label', stat + ' versus ' + cond.label.toLowerCase() +
                '; this recipe ' + FMT[stat](DATA.nominal[stat]) + ' (' + (verdict(stat, DATA.nominal[stat]) || 'no style range') + ')');
        });
        renderTable(cond);
    }

    function cell(text) { var td = document.createElement('td'); td.textContent = text; return td; }

    function renderTable(cond) {
        var table = document.getElementById('cond-table');
        var head = document.createElement('tr');
        [cond.label + ' (' + cond.unit + ')'].concat(STATS).forEach(function (h) {
            var th = document.createElement('th'); th.textContent = h; head.appendChild(th);
        });
        var thead = document.createElement('thead'); thead.appendChild(head);
        var tbody = document.createElement('tbody');
        var nominalIdx = nearestIndex(cond.x, cond.nominal);
        var rows = cond.x.map(function (_, i) { return i; }).filter(function (i) {
            return i % 5 === 0 || i === nominalIdx || i === cond.x.length - 1;
        });
        rows.forEach(function (i) {
            var tr = document.createElement('tr');
            if (i === nominalIdx) tr.className = 'cond-nominal-row';
            tr.appendChild(cell(fmtX(cond, cond.x[i]) + (i === nominalIdx ? ' (this recipe)' : '')));
            STATS.forEach(function (stat) {
                var s = cond.series[stat];
                var text = FMT[stat](s.value[i]);
                if (s.low) text += ' (' + FMT[stat](s.low[i]) + '–' + FMT[stat](s.high[i]) + ')';
                tr.appendChild(cell(text));
            });
            tbody.appendChild(tr);
        });
        table.replaceChildren(thead, tbody);
    }

    STATS.forEach(function (stat) {
        var v = DATA.nominal[stat];
        document.getElementById('cond-value-' + stat).textContent = FMT[stat](v);
        document.getElementById('cond-verdict-' + stat).textContent = verdict(stat, v);
    });
    root.querySelectorAll('[data-cond]').forEach(function (btn) {
        btn.addEventListener('click', function () { render(btn.dataset.cond); });
    });
    var requested = (location.hash.match(/cond=(\w+)/) || [])[1];
    var keys = DATA.conditions.map(function (c) { return c.key; });
    render(keys.indexOf(requested) >= 0 ? requested : keys[0]);
})();
