const tg = window.Telegram.WebApp;
tg.expand();
tg.setHeaderColor('secondary_bg_color');

const initData = tg.initData || '';
const headers  = { 'X-Init-Data': initData };

let weekChart = null, netChart = null, weightChart = null, todayCalChart = null;
let activeDays = 7;

const _valueLabelsPlugin = {
  id: 'valueLabels',
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    chart.getDatasetMeta(0).data.forEach((bar, i) => {
      const val = chart.data.datasets[0].data[i];
      if (val == null) return;
      ctx.save();
      ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--text') || '#fff';
      ctx.font = 'bold 13px -apple-system, BlinkMacSystemFont, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText(val + ' kcal', bar.x, bar.y - 4);
      ctx.restore();
    });
  }
};

// ── Tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'week')   loadWeek();
    if (btn.dataset.tab === 'weight') loadWeight();
  });
});

// ── Fetch helper ──────────────────────────────────────────────────────────────
async function api(path) {
  const r = await fetch(path, { headers });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function fmtDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' });
}

// ── Today ─────────────────────────────────────────────────────────────────────
async function loadToday() {
  const [cfg, data] = await Promise.all([api('/api/config'), api('/api/today')]);

  document.getElementById('header-name').textContent = cfg.name ? `${cfg.name}'s FitBot` : 'FitBot';
  document.getElementById('header-date').textContent = fmtDate(data.date);

  const goalKcal = cfg.calorie_goal || 2000;

  // Calories bar chart
  if (todayCalChart) todayCalChart.destroy();
  todayCalChart = new Chart(document.getElementById('today-cal-chart'), {
    type: 'bar',
    data: {
      labels: ['Consumed', 'Burned'],
      datasets: [{
        data: [data.calories_in, data.calories_out],
        backgroundColor: ['rgba(10,132,255,0.85)', 'rgba(48,209,88,0.85)'],
        borderRadius: 8,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true,
      layout: { padding: { top: 24 } },
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false },
      },
      scales: {
        x: { ticks: { color: '#8e8e93', font: { size: 13 } }, grid: { display: false }, border: { display: false } },
        y: { display: false, suggestedMax: Math.max(data.calories_in, data.calories_out, goalKcal) * 1.15 },
      }
    },
    plugins: [_valueLabelsPlugin],
  });

  const netEl = document.getElementById('val-net');
  const net = data.net;
  netEl.textContent = (net >= 0 ? '+' : '') + net + ' kcal';
  netEl.className = 'net-value ' + (net > cfg.calorie_goal ? 'over' : 'under');
  document.getElementById('val-goal').textContent = `goal: ${cfg.calorie_goal} kcal`;

  const remaining = cfg.calorie_goal - net;
  const remEl = document.getElementById('val-remaining');
  if (remaining > 0) {
    remEl.textContent = remaining + ' kcal';
    remEl.style.color = '#30d158';
  } else {
    remEl.textContent = Math.abs(remaining) + ' kcal over';
    remEl.style.color = '#ff453a';
  }

  // Protein
  const protTarget = cfg.protein_target || 0;
  const prot = data.protein_g;
  if (protTarget > 0) {
    const pctProt = Math.min(prot / protTarget * 100, 100);
    document.getElementById('bar-protein').style.width = pctProt + '%';
    document.getElementById('val-protein').textContent = `${prot}g / ${protTarget}g`;
    const rem = Math.max(protTarget - prot, 0);
    document.getElementById('protein-sub').textContent =
      rem > 0 ? `${rem}g remaining` : 'Target hit!';
  } else {
    document.getElementById('bar-protein').style.width = '0%';
    document.getElementById('val-protein').textContent = `${prot}g`;
    document.getElementById('protein-sub').textContent = 'No target set';
  }

  // Food list
  const foodUl = document.getElementById('food-list');
  foodUl.innerHTML = '';
  if (data.food.length === 0) {
    foodUl.innerHTML = '<li><span class="log-empty">Nothing logged yet</span></li>';
  } else {
    data.food.forEach(e => {
      const li = document.createElement('li');
      const pStr = e.protein_g != null ? ` · ${e.protein_g}g protein` : '';
      li.innerHTML = `<span class="log-name">${e.food}</span>
                      <span class="log-meta">${e.calories} kcal${pStr}</span>`;
      li.querySelector('.log-name').addEventListener('click', () => li.classList.toggle('expanded'));
      foodUl.appendChild(li);
    });
  }

  // Activity list
  const actUl = document.getElementById('activity-list');
  actUl.innerHTML = '';
  if (data.activities.length === 0) {
    actUl.innerHTML = '<li><span class="log-empty">No activity logged</span></li>';
  } else {
    data.activities.forEach(e => {
      const li = document.createElement('li');
      const dur = e.duration_mins ? ` · ${e.duration_mins} min` : '';
      li.innerHTML = `<span class="log-name">${e.activity}</span>
                      <span class="log-meta">${e.calories_burned} kcal${dur}</span>`;
      li.querySelector('.log-name').addEventListener('click', () => li.classList.toggle('expanded'));
      actUl.appendChild(li);
    });
  }
}

// ── Range pills ───────────────────────────────────────────────────────────────
document.querySelectorAll('.pill').forEach(btn => {
  btn.addEventListener('click', () => {
    const days = parseInt(btn.dataset.days);
    if (days === activeDays) return;
    activeDays = days;
    document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if (weekChart) { weekChart.destroy(); weekChart = null; }
    if (netChart)  { netChart.destroy();  netChart  = null; }
    loadWeek();
  });
});

// ── Week ──────────────────────────────────────────────────────────────────────
async function loadWeek() {
  if (weekChart) return;
  const data = await api(`/api/week?days=${activeDays}`);
  const rows = data.week;
  const label = activeDays === 30 ? 'last 30 days' : `last ${activeDays} days`;
  document.getElementById('week-chart-title').textContent = `Calories — ${label}`;

  // Deficit card
  const def = data.accumulated_deficit;
  const fat = data.fat_loss_kg;
  const pct = data.next_kg_pct;
  document.getElementById('acc-deficit').textContent = def.toLocaleString() + ' kcal';
  document.getElementById('acc-fat').textContent     = fat + ' kg fat';
  document.getElementById('acc-bmr').textContent     = data.bmr + ' kcal/day';
  document.getElementById('bar-deficit').style.width = pct + '%';
  document.getElementById('val-deficit-pct').textContent = pct + '%';
  const fullKgs = Math.floor(def / 7700);
  const rem = 7700 - Math.round(def % 7700);
  document.getElementById('deficit-sub').textContent =
    fullKgs > 0
      ? `${fullKgs} full kg burned · ${rem.toLocaleString()} kcal to next kg`
      : `${rem.toLocaleString()} kcal to next 1 kg fat loss`;
  const labels  = rows.map(r => fmtDate(r.date));
  const ins     = rows.map(r => r.calories_in);
  const outs    = rows.map(r => r.calories_burned);
  const nets    = rows.map(r => r.net);

  const chartOpts = {
    responsive: true,
    plugins: { legend: { labels: { color: getComputedStyle(document.body).getPropertyValue('--text') || '#fff' }}},
    scales: {
      x: { ticks: { color: '#8e8e93' }, grid: { color: 'rgba(255,255,255,0.05)' }},
      y: { ticks: { color: '#8e8e93' }, grid: { color: 'rgba(255,255,255,0.05)' }},
    }
  };

  weekChart = new Chart(document.getElementById('week-chart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'In',     data: ins,  backgroundColor: 'rgba(10,132,255,0.7)' },
        { label: 'Burned', data: outs, backgroundColor: 'rgba(48,209,88,0.7)'  },
      ]
    },
    options: { ...chartOpts, plugins: { ...chartOpts.plugins } }
  });

  netChart = new Chart(document.getElementById('net-chart'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Net',
        data: nets,
        borderColor: '#ff9f0a',
        backgroundColor: 'rgba(255,159,10,0.15)',
        tension: 0.3,
        fill: true,
        pointRadius: 4,
      }]
    },
    options: chartOpts
  });
}

// ── Weight ────────────────────────────────────────────────────────────────────
async function loadWeight() {
  if (weightChart) return;
  const [data, cfg] = await Promise.all([api('/api/weight'), api('/api/config')]);
  const rows = [...data.weight].reverse();
  if (!rows.length) return;

  const goal    = cfg.weight_goal || 0;
  const labels  = rows.map(r => fmtDate(r.date));
  const weights = rows.map(r => r.weight_kg);

  // Add goal reference line if set
  const datasets = [{
    label: 'Weight (kg)',
    data: weights,
    borderColor: '#bf5af2',
    backgroundColor: 'rgba(191,90,242,0.15)',
    tension: 0.3,
    fill: true,
    pointRadius: 3,
  }];
  if (goal) {
    datasets.push({
      label: `Goal (${goal} kg)`,
      data: Array(Math.max(labels.length, 2)).fill(goal),
      borderColor: 'rgba(48,209,88,0.8)',
      borderDash: [6, 4],
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 0,
      fill: false,
    });
    // Pad labels array if needed so goal line spans full chart
    while (labels.length < 2) labels.unshift('');
    while (weights.length < labels.length) weights.unshift(null);
  }

  weightChart = new Chart(document.getElementById('weight-chart'), {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { display: goal > 0, labels: { color: '#8e8e93', boxWidth: 12 } }},
      scales: {
        x: { ticks: { color: '#8e8e93', maxTicksLimit: 8 }, grid: { color: 'rgba(255,255,255,0.05)' }},
        y: { ticks: { color: '#8e8e93' }, grid: { color: 'rgba(255,255,255,0.05)' }},
      }
    }
  });

  const ws     = rows.map(r => r.weight_kg);
  const latest = ws[ws.length - 1];
  const high   = Math.max(...ws);
  const low    = Math.min(...ws);
  const change = (latest - ws[0]).toFixed(1);
  document.getElementById('w-latest').textContent = latest + ' kg';
  document.getElementById('w-high').textContent   = high   + ' kg';
  document.getElementById('w-low').textContent    = low    + ' kg';
  document.getElementById('w-change').textContent = (change > 0 ? '+' : '') + change + ' kg';

  if (goal) {
    const diff = (latest - goal).toFixed(1);
    document.getElementById('w-goal').textContent = goal + ' kg';
    document.getElementById('w-togo').textContent = diff > 0
      ? `${diff} kg above goal` : diff < 0 ? `${Math.abs(diff)} kg to go` : 'Goal reached! 🎉';

    // Progress bar: how far from start toward goal
    const start = ws[0];
    const totalDelta = Math.abs(goal - start);
    const doneDelta  = Math.abs(latest - start);
    const pct = totalDelta > 0 ? Math.min(doneDelta / totalDelta * 100, 100) : 100;
    document.getElementById('bar-wgoal').style.width = pct + '%';
    document.getElementById('val-wgoal-pct').textContent = Math.round(pct) + '%';
    document.getElementById('wgoal-sub').textContent =
      `Started at ${start} kg → goal ${goal} kg`;
    document.getElementById('weight-goal-bar').style.display = 'block';
  } else {
    document.getElementById('w-goal').textContent = 'Not set';
    document.getElementById('w-togo').textContent = '—';
  }

  document.getElementById('weight-stats').style.display = 'block';
}

// ── Refresh ───────────────────────────────────────────────────────────────────
function activeTab() {
  return document.querySelector('.tab.active')?.dataset.tab || 'today';
}

async function refresh() {
  const btn = document.getElementById('btn-refresh');
  btn.classList.add('spinning');
  setTimeout(() => btn.classList.remove('spinning'), 300);

  const tab = activeTab();
  if (tab === 'today') {
    if (todayCalChart) { todayCalChart.destroy(); todayCalChart = null; }
    await loadToday();
  } else if (tab === 'week') {
    if (weekChart) { weekChart.destroy(); weekChart = null; }
    if (netChart)  { netChart.destroy();  netChart  = null; }
    await loadWeek();
  } else if (tab === 'weight') {
    if (weightChart) { weightChart.destroy(); weightChart = null; }
    await loadWeight();
  }
}

document.getElementById('btn-refresh').addEventListener('click', refresh);

// ── Init ──────────────────────────────────────────────────────────────────────
(async () => {
  try {
    await loadToday();
    document.getElementById('loading').classList.add('hidden');
  } catch (e) {
    document.getElementById('loading').classList.add('hidden');
    const errEl = document.getElementById('error');
    errEl.style.display = 'flex';
    errEl.textContent = 'Failed to load: ' + e.message;
  }
})();
