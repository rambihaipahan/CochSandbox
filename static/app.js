const SERIES_COLORS = [
  "--series-1", "--series-2", "--series-3", "--series-4",
  "--series-5", "--series-6", "--series-7", "--series-8",
];

const state = {
  platforms: [],
  activePlatform: null,
  tableView: "aggregate", // "aggregate" | "site"
  capacityChart: null,
  pctChart: null,
};

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function monthLabel(iso) {
  const [y, m] = iso.split("-");
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

/** Splits an actual/forecast pair of arrays into two Chart.js-friendly arrays
 * that visually join at the last actual point, so one series reads as a
 * single line that switches from solid to dashed. */
function actualForecastPair(rows, actualKey, forecastKey) {
  let lastActualIdx = -1;
  rows.forEach((r, i) => { if (r.is_actual) lastActualIdx = i; });
  const actual = rows.map((r, i) => (i <= lastActualIdx ? r[actualKey] : null));
  const forecast = rows.map((r, i) => {
    if (i < lastActualIdx) return null;
    if (i === lastActualIdx) return r[actualKey];
    return r[forecastKey];
  });
  return { actual, forecast };
}

async function loadPlatforms() {
  state.platforms = await fetchJSON("/api/platforms");
  const tabs = document.getElementById("platform-tabs");
  tabs.innerHTML = "";
  state.platforms.forEach((p, i) => {
    const btn = document.createElement("button");
    btn.className = "tab" + (i === 0 ? " active" : "");
    btn.textContent = p.label;
    btn.dataset.key = p.key;
    btn.addEventListener("click", () => selectPlatform(p.key));
    tabs.appendChild(btn);
  });

  const entryPlatform = document.getElementById("entry-platform");
  entryPlatform.innerHTML = state.platforms
    .map((p) => `<option value="${p.key}">${p.label}</option>`)
    .join("");
  entryPlatform.addEventListener("change", populateSiteOptions);
  populateSiteOptions();

  if (state.platforms.length) selectPlatform(state.platforms[0].key);
}

function populateSiteOptions() {
  const platformKey = document.getElementById("entry-platform").value;
  const platform = state.platforms.find((p) => p.key === platformKey);
  const siteSelect = document.getElementById("entry-site");
  siteSelect.innerHTML = (platform ? platform.sites : []).map((s) => `<option value="${s}">${s}</option>`).join("");
}

async function selectPlatform(key) {
  state.activePlatform = key;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.key === key));
  await renderPlatform(key);
}

/** Keep charts/tables focused on a rolling window around "now" (trailing
 * actuals + a forward forecast horizon) instead of the workbook's full
 * decade-plus calendar. Sites with a shorter history/forecast than others
 * (e.g. one platform stops projecting sooner) would otherwise drag the
 * aggregate total down at the tail as they drop out of the sum. */
const TRAILING_MONTHS = 18;
const FORECAST_MONTHS = 24;

function addMonths(iso, delta) {
  const [y, m] = iso.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

function windowSeries(rows, pivotMonth, hardMax) {
  const from = addMonths(pivotMonth, -TRAILING_MONTHS);
  let to = addMonths(pivotMonth, FORECAST_MONTHS);
  if (hardMax && hardMax < to) to = hardMax;
  return rows.filter((r) => r.month >= from && r.month <= to);
}

async function renderPlatform(key) {
  const platform = state.platforms.find((p) => p.key === key);
  const [summary, aggSeries, siteSeriesList] = await Promise.all([
    fetchJSON("/api/summary"),
    fetchJSON(`/api/series/platform?platform=${encodeURIComponent(key)}`),
    Promise.all(platform.sites.map((site) => fetchJSON(`/api/series/site?platform=${encodeURIComponent(key)}&site=${encodeURIComponent(site)}`))),
  ]);
  const platformSummary = summary.find((s) => s.platform === key);
  const pivot = platformSummary.latest_month || new Date().toISOString().slice(0, 7) + "-01";
  // Cap the forward window at the shortest *still-active* site's horizon, so
  // the aggregate never quietly drops just because one site's forecast
  // calendar wasn't extended as far as the others'. A site whose last row is
  // already in the past (before the latest actual month) is stale/retired
  // rather than under-forecast, so it's left to drop out of the sum on its
  // own and never shortens the window for the sites still being tracked.
  const shortestHorizon = siteSeriesList.reduce((min, s) => {
    const last = s.series[s.series.length - 1]?.month;
    if (!last || last < pivot) return min;
    return !min || last < min ? last : min;
  }, null);

  const aggWindowed = windowSeries(aggSeries.series, pivot, shortestHorizon);
  const siteWindowed = siteSeriesList.map((s) => ({ ...s, series: windowSeries(s.series, pivot, shortestHorizon) }));

  renderKPIs(platformSummary, platform.label);
  renderCapacityChart(platform.label, aggWindowed);
  renderPctChart(platform.label, siteWindowed, platformSummary.max_pct_threshold);
  renderTable(aggWindowed, siteWindowed);
}

function renderKPIs(s, label) {
  const row = document.getElementById("kpi-row");
  if (!s || s.latest_month === null) {
    row.innerHTML = `<div class="kpi-tile"><div class="label">${label}</div><div class="value">No actuals recorded yet</div></div>`;
    return;
  }
  const trend = s.trend_pct_points;
  const trendClass = trend == null ? "flat" : trend > 0.05 ? "up" : trend < -0.05 ? "down" : "flat";
  const trendText = trend == null ? "no prior month" : `${trend > 0 ? "+" : ""}${trend} pts vs prior month`;
  const nearThreshold = s.months_to_threshold != null && s.months_to_threshold <= 6;

  row.innerHTML = `
    <div class="kpi-tile">
      <div class="label">Latest month</div>
      <div class="value">${monthLabel(s.latest_month)}</div>
    </div>
    <div class="kpi-tile">
      <div class="label">Total capacity</div>
      <div class="value">${s.total_capacity_tb}<span class="unit">TB</span></div>
    </div>
    <div class="kpi-tile">
      <div class="label">In use</div>
      <div class="value">${s.in_use_tb}<span class="unit">TB</span></div>
    </div>
    <div class="kpi-tile">
      <div class="label">Available</div>
      <div class="value">${s.available_tb}<span class="unit">TB</span></div>
    </div>
    <div class="kpi-tile ${nearThreshold ? "warn" : ""}">
      <div class="label">Utilization</div>
      <div class="value">${s.in_use_pct}<span class="unit">%</span></div>
      <div class="delta ${trendClass}">${trendText}</div>
    </div>
    <div class="kpi-tile ${nearThreshold ? "warn" : ""}">
      <div class="label">Threshold (${s.max_pct_threshold ?? "?"}%) reached in</div>
      <div class="value">${s.months_to_threshold != null ? s.months_to_threshold : "—"}<span class="unit">${s.months_to_threshold != null ? "months" : ""}</span></div>
    </div>
  `;
}

function renderCapacityChart(label, series) {
  document.getElementById("capacity-chart-title").textContent = `${label}: capacity vs. in-use (TB)`;
  const labels = series.map((r) => monthLabel(r.month));
  const { actual, forecast } = actualForecastPair(series, "in_use_tb", "forecast_tb");
  const blue = css("--series-1");
  const muted = css("--text-muted");

  const ctx = document.getElementById("capacity-chart").getContext("2d");
  if (state.capacityChart) state.capacityChart.destroy();
  state.capacityChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Total capacity",
          data: series.map((r) => r.total_capacity_tb),
          borderColor: muted,
          borderDash: [3, 3],
          borderWidth: 1.5,
          pointRadius: 0,
          fill: false,
          tension: 0,
        },
        {
          label: "In use (actual)",
          data: actual,
          borderColor: blue,
          backgroundColor: blue + "26",
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.15,
        },
        {
          label: "In use (forecast)",
          data: forecast,
          borderColor: blue,
          borderDash: [6, 4],
          borderWidth: 2,
          pointRadius: 0,
          fill: false,
          tension: 0.15,
        },
      ],
    },
    options: chartOptions("TB"),
  });
}

function renderPctChart(label, siteSeriesList, threshold) {
  document.getElementById("pct-chart-title").textContent = `${label}: % utilization by site`;
  const labels = siteSeriesList[0]?.series.map((r) => monthLabel(r.month)) || [];
  const datasets = [];

  siteSeriesList.forEach((s, i) => {
    const color = css(SERIES_COLORS[i % SERIES_COLORS.length]);
    const { actual, forecast } = actualForecastPair(s.series, "in_use_pct", "forecast_pct");
    datasets.push({
      label: `${s.site} (actual)`,
      data: actual,
      borderColor: color,
      borderWidth: 2,
      pointRadius: 0,
      fill: false,
      tension: 0.15,
    });
    datasets.push({
      label: `${s.site} (forecast)`,
      data: forecast,
      borderColor: color,
      borderDash: [6, 4],
      borderWidth: 2,
      pointRadius: 0,
      fill: false,
      tension: 0.15,
      _hideInLegend: true,
    });
  });

  if (threshold != null) {
    datasets.push({
      label: `Threshold (${threshold}%)`,
      data: labels.map(() => threshold),
      borderColor: css("--status-critical"),
      borderDash: [4, 4],
      borderWidth: 1.5,
      pointRadius: 0,
      fill: false,
    });
  }

  const ctx = document.getElementById("pct-chart").getContext("2d");
  if (state.pctChart) state.pctChart.destroy();
  state.pctChart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: chartOptions("%", (legendItem, data) => !data.datasets[legendItem.datasetIndex]._hideInLegend),
  });
}

function chartOptions(unit, legendFilter) {
  const grid = css("--gridline");
  const ink = css("--text-secondary");
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: {
        position: "bottom",
        labels: {
          color: ink,
          boxWidth: 10,
          usePointStyle: true,
          filter: legendFilter || (() => true),
        },
      },
      tooltip: {
        callbacks: {
          label: (ctx) => {
            const v = ctx.parsed.y;
            return v == null ? undefined : `${ctx.dataset.label}: ${Number(v).toFixed(1)} ${unit}`;
          },
        },
      },
    },
    scales: {
      x: { grid: { color: grid }, ticks: { color: ink, maxRotation: 0, autoSkip: true } },
      y: { grid: { color: grid }, ticks: { color: ink }, title: { display: true, text: unit, color: ink }, grace: "8%" },
    },
  };
}

function renderTable(aggSeries, siteSeriesList) {
  const toggle = document.getElementById("table-view-toggle");
  toggle.innerHTML = `
    <button data-view="aggregate" class="${state.tableView === "aggregate" ? "active" : ""}">Aggregate</button>
    <button data-view="site" class="${state.tableView === "site" ? "active" : ""}">By site</button>
  `;
  toggle.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      state.tableView = b.dataset.view;
      renderTable(aggSeries, siteSeriesList);
    })
  );

  const table = document.getElementById("detail-table");
  if (state.tableView === "aggregate") {
    table.innerHTML = `
      <thead><tr><th>Month</th><th>Total (TB)</th><th>In use (TB)</th><th>Utilization %</th></tr></thead>
      <tbody>
        ${aggSeries
          .slice()
          .reverse()
          .map(
            (r) => `<tr class="${r.is_actual ? "" : "forecast"}">
              <td>${monthLabel(r.month)}${r.is_actual ? "" : " (forecast)"}</td>
              <td>${r.total_capacity_tb ?? "—"}</td>
              <td>${r.is_actual ? r.in_use_tb : (r.forecast_tb != null ? r.forecast_tb.toFixed(1) : "—")}</td>
              <td>${r.is_actual ? r.in_use_pct : (r.forecast_pct != null ? r.forecast_pct.toFixed(1) : "—")}</td>
            </tr>`
          )
          .join("")}
      </tbody>`;
  } else {
    const months = siteSeriesList[0]?.series.map((r) => r.month) || [];
    table.innerHTML = `
      <thead><tr><th>Month</th>${siteSeriesList.map((s) => `<th>${s.site} %</th>`).join("")}</tr></thead>
      <tbody>
        ${months
          .map((m, i) => {
            const anyActual = siteSeriesList.some((s) => s.series[i].is_actual);
            return `<tr class="${anyActual ? "" : "forecast"}">
              <td>${monthLabel(m)}</td>
              ${siteSeriesList
                .map((s) => {
                  const r = s.series[i];
                  const v = r.is_actual ? r.in_use_pct : r.forecast_pct;
                  return `<td>${v != null ? v.toFixed(1) : "—"}</td>`;
                })
                .join("")}
            </tr>`;
          })
          .reverse()
          .join("")}
      </tbody>`;
  }
}

async function handleEntrySubmit(e) {
  e.preventDefault();
  const msg = document.getElementById("entry-msg");
  msg.textContent = "";
  msg.className = "form-msg";

  const platform = document.getElementById("entry-platform").value;
  const site = document.getElementById("entry-site").value;
  const monthVal = document.getElementById("entry-month").value; // YYYY-MM
  const inUse = parseFloat(document.getElementById("entry-in-use").value);
  const totalRaw = document.getElementById("entry-total").value;

  const payload = {
    platform,
    site,
    month: `${monthVal}-01`,
    in_use_tb: inUse,
  };
  if (totalRaw !== "") payload.total_capacity_tb = parseFloat(totalRaw);

  try {
    await fetchJSON("/api/entries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    msg.textContent = `Saved ${site} / ${monthVal}. Charts updated.`;
    msg.className = "form-msg ok";
    if (platform === state.activePlatform) await renderPlatform(platform);
  } catch (err) {
    msg.textContent = err.message;
    msg.className = "form-msg error";
  }
}

document.getElementById("entry-form").addEventListener("submit", handleEntrySubmit);
loadPlatforms();
