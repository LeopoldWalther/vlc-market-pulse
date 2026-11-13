/**
 * App entry point — wires the DataSource, chart renderers, KPI summary, and
 * responsive/theming behaviour.
 *
 * window.CONFIG.DATA_URL is injected per-environment (dev/prod) either inline
 * in index.html or via a config.js sync'd by the deploy workflow.
 *
 * This module is deliberately thin: all aggregation math lives in summary.js,
 * all theme/viewport/rerender decisions live in dashboard_state.js, and all
 * chart layout construction lives in chart_theme.js (via each renderer). This
 * module only reads DOM/browser state, calls those pure functions, and
 * applies their results to the page.
 */

import { DataSource } from './src/data_source.js';
import { PipelineHealthDataSource } from './src/pipeline_health_data_source.js';
import { priceTimeSeriesRentRenderer, priceTimeSeriesSaleRenderer } from './src/charts/price_time_series.js';
import { priceTimeSeriesDistrictRentRenderer, priceTimeSeriesDistrictSaleRenderer } from './src/charts/price_time_series_district.js';
import { listingCountTimeSeriesDistrictRenderer } from './src/charts/listing_count_time_series_district.js';
import { listingCountTimeSeriesNeighborhoodRenderer } from './src/charts/listing_count_time_series_neighborhood.js';
import { rentVsSaleRatioRenderer } from './src/charts/rent_vs_sale_ratio.js';
import { ratioTimeSeriesRenderer } from './src/charts/rent_vs_sale_ratio_time_series.js';
import { boxplotRentRenderer, boxplotSaleRenderer } from './src/charts/boxplot_by_neighborhood.js';
import { weeklyListingVolumeRenderer } from './src/charts/weekly_listing_volume.js';
import { sizeHistogramRenderer } from './src/charts/size_histogram.js';
import { roomsDistributionRenderer } from './src/charts/rooms_distribution.js';
import { priceHistogramRentRenderer, priceHistogramSaleRenderer } from './src/charts/price_per_area_histogram.js';
import { listingLocationsMapRenderer } from './src/charts/listing_locations_map.js';
import { summaryStats, formatKpi } from './src/summary.js';
import { resolveTheme, resolveViewport, shouldRerender, createLoadState, transition } from './src/dashboard_state.js';
import { MAX_SCOPE_SELECTION, extractDistricts, extractNeighborhoods, filterPopulationBlock, toggleScopeSelection } from './src/filters.js';
import { t, isRtl, resolveLocale } from './src/i18n.js';
import { resolveActiveTab, buildTabHash } from './src/tab_state.js';
import { formatSearchConfigSummary } from './src/search_config.js';
import { overallBadgeLabel, buildSubLightRows, unavailableMessage, thresholdRuleText } from './src/pipeline_health.js';
import { buildDiagramModel, renderDiagramSvg } from './src/pipeline_health_diagram.js';
import { pipelineExecutionSuccessChartRenderer } from './src/charts/pipeline_execution_success_chart.js';
import { pipelineExecutionDurationChartRenderer } from './src/charts/pipeline_execution_duration_chart.js';
import { pipelineApiQuotaChartRenderer } from './src/charts/pipeline_api_quota_chart.js';
import { pipelineAwsCostChartRenderer } from './src/charts/pipeline_aws_cost_chart.js';

const THEME_STORAGE_KEY = 'vlc-dashboard-theme';
const LOCALE_STORAGE_KEY = 'vlc-dashboard-locale';
const RESIZE_DEBOUNCE_MS = 200;

// BUGFIX: Plotly's default hover-triggered modebar (camera/zoom/pan icons)
// renders in a fixed position at the very top of the chart, which is the
// same region as the chart title — on hover the icons visually overlapped
// and obscured the (often long, translated) title text. Since this is a
// read-only public dashboard (not an analysis tool visitors need to
// zoom/pan/export from), the modebar is disabled outright rather than
// fragile pixel-tuning the title position to dodge it.
const PLOTLY_CONFIG = { responsive: true, displayModeBar: false };

// Renderers that exist in both 'general' and 'relevant' populations.
// The population toggle switches which block is passed to render().
const TOGGLE_RENDERERS = [
  rentVsSaleRatioRenderer,
  ratioTimeSeriesRenderer,
  boxplotRentRenderer,
  boxplotSaleRenderer,
];

// Renderers only available in the 'general' population block.
// Neighbourhood and district are split into rent + sale to avoid a 300×
// Y-axis mismatch (€/m²/month rent vs €/m² sale).
const GENERAL_ONLY_RENDERERS = [
  priceTimeSeriesRentRenderer,
  priceTimeSeriesSaleRenderer,
  priceTimeSeriesDistrictRentRenderer,
  priceTimeSeriesDistrictSaleRenderer,
  listingCountTimeSeriesDistrictRenderer,
  listingCountTimeSeriesNeighborhoodRenderer,
];

const ALL_RENDERERS = [...GENERAL_ONLY_RENDERERS, ...TOGGLE_RENDERERS];

// Data Basis tab renderers. Since FEATURE-016 every Data Basis dataset
// carries a district/neighbourhood dimension and exists once per population
// (data_basis / data_basis_relevant), so all of them follow the shared
// filter-bar: scope via applyScope(), population via activeDataBasisBlock().
// The count charts sum the surviving rows back to their own grain
// (src/reaggregate.js).
const DATA_BASIS_RENDERERS = [
  listingLocationsMapRenderer,
  weeklyListingVolumeRenderer,
  priceHistogramSaleRenderer,
  priceHistogramRentRenderer,
  sizeHistogramRenderer,
  roomsDistributionRenderer,
];

// Renderer ids whose x-axis is a plain date timeline — they share the
// generic 'charts.xaxis.date' i18n key instead of a per-renderer xaxis key
// (see plotChart()'s title/axis translation override).
const SHARED_DATE_XAXIS_RENDERER_IDS = new Set([
  'price-time-series-rent',
  'price-time-series-sale',
  'price-time-series-district-rent',
  'price-time-series-district-sale',
  'listing-count-time-series-district',
  'listing-count-time-series-neighborhood',
  'rent-vs-sale-ratio-time-series',
  'weekly-listing-volume',
]);

const containers = {
  'price-time-series-rent':          document.getElementById('price-time-series-rent'),
  'price-time-series-sale':          document.getElementById('price-time-series-sale'),
  'price-time-series-district-rent': document.getElementById('price-time-series-district-rent'),
  'price-time-series-district-sale': document.getElementById('price-time-series-district-sale'),
  'listing-count-time-series-district': document.getElementById('listing-count-time-series-district'),
  'listing-count-time-series-neighborhood': document.getElementById('listing-count-time-series-neighborhood'),
  'rent-vs-sale-ratio':              document.getElementById('rent-vs-sale-ratio'),
  'rent-vs-sale-ratio-time-series':  document.getElementById('rent-vs-sale-ratio-time-series'),
  'boxplot-by-neighborhood-rent':    document.getElementById('boxplot-by-neighborhood-rent'),
  'boxplot-by-neighborhood-sale':    document.getElementById('boxplot-by-neighborhood-sale'),
  'listing-locations-map':          document.getElementById('listing-locations-map'),
  'weekly-listing-volume':           document.getElementById('weekly-listing-volume'),
  'price-per-area-histogram-sale':   document.getElementById('price-per-area-histogram-sale'),
  'price-per-area-histogram-rent':   document.getElementById('price-per-area-histogram-rent'),
  'size-histogram':                  document.getElementById('size-histogram'),
  'rooms-distribution':              document.getElementById('rooms-distribution'),
};

// Pipeline Health detail chart renderers (FEATURE-013, task 13.12) — each
// paired with the i18n title key its section heading already uses (task
// 13.5) so titles stay in one place instead of duplicating English copy
// inside the chart renderer modules themselves.
const PIPELINE_HEALTH_CHARTS = [
  { renderer: pipelineExecutionSuccessChartRenderer, titleKey: 'pipelineHealth.detail.executionSuccess.title', checkId: 'execution_success' },
  { renderer: pipelineExecutionDurationChartRenderer, titleKey: 'pipelineHealth.detail.executionDuration.title', checkId: 'execution_duration' },
  { renderer: pipelineApiQuotaChartRenderer, titleKey: 'pipelineHealth.detail.apiQuota.title', checkId: 'api_quota' },
  { renderer: pipelineAwsCostChartRenderer, titleKey: 'pipelineHealth.detail.awsCost.title', checkId: 'aws_cost' },
];


const KPI_FORMATTERS = {
  'median-rent': (stats) => formatKpi(stats.medianRentEurPerM2Month, 'eur_per_m2_month'),
  'median-sale': (stats) => formatKpi(stats.medianSaleEurPerM2, 'eur_per_m2'),
  'gross-yield': (stats) => formatKpi(stats.impliedGrossYieldPercent, 'percent'),
  'listing-count': (stats) => formatKpi(stats.totalListingCount, 'count'),
  'last-updated': (stats) => formatKpi(stats.lastUpdated, 'date'),
};

// Announcer copy keyed by load-lifecycle status; looked up via t() so it
// tracks the active locale (see announce()/setLoadState()).
const LOAD_STATE_MESSAGE_KEYS = {
  loading: 'status.loading',
  ready: 'status.ready',
  error: 'status.error',
};

const dataSource = new DataSource(window.CONFIG.DATA_URL);

// Independent data source for the Pipeline Health tab (task 12.11) — its
// load lifecycle is fully decoupled from `dataSource`/`cachedData` above:
// it is only fetched lazily, the first time the pipeline-health tab is
// activated, and a failure here never affects the Trend Analysis/Data
// Basis load state.
const pipelineHealthDataSource = new PipelineHealthDataSource(
  window.CONFIG.PIPELINE_HEALTH_URL ?? '/gold/pipeline_health/latest.json',
);
let pipelineHealthDocument = null;

// Active population — starts on 'general'; toggle switches to 'relevant'.
let activePopulation = 'general';
let cachedData = null;
let loadState = createLoadState();

// Tab navigation state — 'trend-analysis' is rendered eagerly by run();
// 'data-basis' is rendered lazily, once, the first time it becomes visible
// (Plotly cannot size a chart inside a hidden/display:none container).
const renderedTabs = new Set(['trend-analysis']);

// District/neighborhood scope filters — both cap at MAX_SCOPE_SELECTION.
// Empty array means "no restriction" on that axis (see filters.js).
let selectedDistricts = [];
let selectedNeighborhoods = [];

/**
 * Read the explicitly stored locale choice, if any.
 *
 * @returns {string|null} A locale code, or null if never set / unavailable.
 */
function getStoredLocale() {
  try {
    return window.localStorage.getItem(LOCALE_STORAGE_KEY);
  } catch {
    return null;
  }
}

// Active UI locale — separate from currentContext because a locale change
// always requires a full re-render (every chart title/axis label changes),
// unlike viewport/theme changes which dashboard_state.shouldRerender filters.
let currentLocale = resolveLocale(getStoredLocale());

// Responsive/theme context passed to every renderer's buildLayout call.
// Reads (localStorage/matchMedia) happen once here; dashboard_state.js
// resolves what they mean.
// DEFAULT_COLOR_SCHEME wins whenever the visitor has not made an explicit
// choice yet (see resolveTheme) — dark is the portfolio's default look,
// regardless of the OS/browser preference. An explicit toggle click (see
// setExplicitTheme) always overrides this and persists across reloads.
const DEFAULT_COLOR_SCHEME = 'dark';

let currentContext = {
  viewport: resolveViewport(window.innerWidth),
  colorScheme: resolveTheme(getStoredTheme(), DEFAULT_COLOR_SCHEME),
};

/**
 * Read the explicitly stored theme choice, if any.
 *
 * @returns {string|null} 'light', 'dark', or null if never set / unavailable.
 */
function getStoredTheme() {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // Storage unavailable (e.g. privacy mode) — behave as if nothing is stored.
    return null;
  }
}

/**
 * Reflect the active color scheme on the document root so CSS custom
 * properties (light/dark tokens) apply.
 *
 * @param {'light'|'dark'} colorScheme
 */
function applyTheme(colorScheme) {
  document.documentElement.setAttribute('data-theme', colorScheme);
}

/**
 * Walk every element carrying a `data-i18n` (textContent) or
 * `data-i18n-attr` (comma-separated `attr:key` pairs) marker and apply the
 * active locale's translation. Also reflects the locale on <html lang>/dir
 * so assistive tech and CSS logical properties respond correctly (Arabic
 * is the only RTL locale — see isRtl()).
 *
 * @param {string} locale
 */
function applyTranslations(locale) {
  document.documentElement.lang = locale;
  document.documentElement.dir = isRtl(locale) ? 'rtl' : 'ltr';

  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(locale, el.getAttribute('data-i18n'));
  });

  document.querySelectorAll('[data-i18n-attr]').forEach((el) => {
    const pairs = el.getAttribute('data-i18n-attr').split(',');
    for (const pair of pairs) {
      const [attr, key] = pair.split(':').map((s) => s.trim());
      if (attr && key) el.setAttribute(attr, t(locale, key));
    }
  });

  const langCurrentEl = document.querySelector('[data-lang-current]');
  if (langCurrentEl) langCurrentEl.textContent = locale.toUpperCase();
}

/**
 * Push a message into the aria-live region so screen readers announce
 * load-lifecycle changes.
 *
 * @param {string} message
 */
function announce(message) {
  const el = document.getElementById('status-announcer');
  if (el) el.textContent = message;
}

/**
 * Apply a load-lifecycle state to the DOM: toggle the error block and
 * announce the change.
 *
 * @param {{status: 'loading'|'ready'|'error'}} nextState
 */
function setLoadState(nextState) {
  loadState = nextState;
  const errorEl = document.getElementById('dashboard-error');
  if (errorEl) errorEl.hidden = loadState.status !== 'error';
  const messageKey = LOAD_STATE_MESSAGE_KEYS[loadState.status];
  announce(messageKey ? t(currentLocale, messageKey) : '');
}

/**
 * Build a human-readable label from a relevant_filter object so the toggle
 * shows the actual criteria (e.g. "Flats: ≥120 m², lift, ≥2 rooms") rather
 * than a generic placeholder.
 *
 * @param {object|null|undefined} filter - relevant_filter from the gold JSON.
 * @returns {string} Short filter summary.
 */
function buildRelevantLabel(filter) {
  if (!filter) return t(currentLocale, 'population.filteredFallback');
  const parts = [];
  if (filter.size_gt != null) parts.push(t(currentLocale, 'population.sizeGte', { value: filter.size_gt }));
  if (filter.hasLift) parts.push(t(currentLocale, 'population.lift'));
  if (filter.rooms_gte != null) parts.push(t(currentLocale, 'population.roomsGte', { value: filter.rooms_gte }));
  if (filter.bathrooms_gte != null) parts.push(t(currentLocale, 'population.bathroomsGte', { value: filter.bathrooms_gte }));
  if (filter.floor_not != null) parts.push(t(currentLocale, 'population.floorNot', { value: filter.floor_not }));
  return parts.length > 0
    ? `${t(currentLocale, 'population.filteredPrefix')}: ${parts.join(', ')}`
    : t(currentLocale, 'population.filteredFallback');
}

/**
 * Apply the current district/neighborhood scope selection to a raw
 * population block. Pure passthrough (no clone) when nothing is selected.
 *
 * @param {object|null|undefined} rawBlock
 * @returns {object|null|undefined}
 */
function applyScope(rawBlock) {
  return filterPopulationBlock(rawBlock, {
    districts: selectedDistricts,
    neighborhoods: selectedNeighborhoods,
  });
}

/**
 * Render the KPI headline row for a given population block.
 *
 * `generated_at` lives on the top-level payload (not inside `general`/
 * `relevant`), so it's merged in here from the module-level `cachedData`
 * rather than expected on `populationBlock` itself.
 *
 * @param {object|null|undefined} populationBlock - `cachedData.general` or
 *   `cachedData[activePopulation]`.
 */
function renderKpis(populationBlock) {
  const stats = summaryStats({ ...populationBlock, generated_at: cachedData?.generated_at });
  for (const [kpi, format] of Object.entries(KPI_FORMATTERS)) {
    const el = document.querySelector(`.kpi-card[data-kpi="${kpi}"] .kpi-value`);
    if (el) el.textContent = format(stats);
  }
}

/**
 * Reveal a chart's container by hiding its loading skeleton.
 *
 * @param {string} rendererId
 */
function markChartLoaded(rendererId) {
  const section = containers[rendererId]?.closest('.chart-section');
  section?.classList.add('is-loaded');
}

/**
 * Drop the redundant "operation – " prefix from every trace's legend name
 * when every trace on this chart shares the same `meta.operation` value.
 *
 * transforms.js always names neighbourhood/district traces "rent – X" or
 * "sale – X" so a single formatter can serve both the rent-only and
 * sale-only renderer variants, but once split into separate rent/sale
 * charts that prefix is identical on every entry and carries no
 * information — it just makes every legend label ~40-50% wider than
 * necessary. On mobile, with a dozen neighbourhoods, that extra width
 * forced far more wrapped legend rows than needed, shrinking the actual
 * plot area to a sliver. Renderers/transforms are left untouched (their
 * existing tests still assert the full "operation – X" name) — this is a
 * display-only post-processing step, consistent with the i18n title/axis
 * override above.
 *
 * @param {Array<object>|undefined} traces - fig.data from a renderer's render().
 */
function simplifyLegendLabels(traces) {
  if (!Array.isArray(traces) || traces.length === 0) return;
  const operations = new Set(traces.map((trace) => trace.meta?.operation).filter(Boolean));
  if (operations.size !== 1) return; // Mixed operations on one chart — prefix is meaningful, keep it.
  for (const trace of traces) {
    if (trace.meta?.neighborhood) {
      trace.name = trace.meta.neighborhood;
    } else if (trace.meta?.district) {
      trace.name = trace.meta.district;
    }
  }
}

/**
 * Render (or re-render) a single chart into its container.
 *
 * First render uses Plotly.newPlot; subsequent calls (population toggle,
 * viewport/theme change) use Plotly.react so Plotly can diff efficiently.
 *
 * @param {{id: string, title: string, render: function}} renderer
 * @param {object|null|undefined} block - Population block passed to render().
 * @param {{viewport: string, colorScheme: string}} context
 * @param {{initial: boolean}} options
 */
async function plotChart(renderer, block, context, { initial }) {
  const container = containers[renderer.id];
  if (!container) return;

  const fig = renderer.render(block, context);

  // Override hardcoded English title/axis text with the active locale's
  // translation when one exists for this renderer id — renderers themselves
  // stay untouched (see i18n.js design note), keeping their own unit tests valid.
  const translatedTitle = t(currentLocale, `charts.${renderer.id}.title`);
  fig.layout.title = { text: translatedTitle };
  if (fig.layout.xaxis?.title) {
    const xKey = SHARED_DATE_XAXIS_RENDERER_IDS.has(renderer.id)
      ? 'charts.xaxis.date'
      : `charts.${renderer.id}.xaxis`;
    fig.layout.xaxis.title = { text: t(currentLocale, xKey) };
  }
  if (fig.layout.yaxis?.title) {
    fig.layout.yaxis.title = { text: t(currentLocale, `charts.${renderer.id}.yaxis`) };
  }

  simplifyLegendLabels(fig.data);

  // Stamp the active population into toggle-chart titles so users can see
  // the switch take effect even before scrolling to changed data points.
  if (TOGGLE_RENDERERS.includes(renderer)) {
    const popLabel = activePopulation === 'general'
      ? t(currentLocale, 'population.all')
      : buildRelevantLabel(cachedData?.relevant_filter);
    fig.layout.title = { text: `${translatedTitle} — ${popLabel}` };
  }

  try {
    if (initial) {
      await globalThis.Plotly.newPlot(container, fig.data, fig.layout, PLOTLY_CONFIG);
    } else {
      await globalThis.Plotly.react(container, fig.data, fig.layout, PLOTLY_CONFIG);
    }
    markChartLoaded(renderer.id);
  } catch (err) {
    console.error(`[Dashboard] Failed to render chart '${renderer.id}':`, err);
    // Hide the skeleton anyway so a chart-level failure doesn't pulse forever.
    markChartLoaded(renderer.id);
  }
}

/**
 * Render every chart against the given responsive/theme context.
 *
 * @param {{viewport: string, colorScheme: string}} context
 * @param {{initial: boolean}} [options]
 */
async function renderAllCharts(context, { initial = true } = {}) {
  const generalScoped = applyScope(cachedData.general);
  const activeScoped = applyScope(cachedData[activePopulation]);
  for (const renderer of GENERAL_ONLY_RENDERERS) {
    await plotChart(renderer, generalScoped, context, { initial });
  }
  for (const renderer of TOGGLE_RENDERERS) {
    await plotChart(renderer, activeScoped, context, { initial });
  }
}

/**
 * Render the search-config definition list inside the Data Basis panel from
 * data_basis.search_config[0], via the pure search_config.js formatter.
 */
function renderSearchConfigPanel() {
  const container = document.getElementById('data-basis-search-config');
  if (!container) return;
  const searchConfig = cachedData?.data_basis?.search_config?.[0];
  const rows = formatSearchConfigSummary(searchConfig, currentLocale);
  container.textContent = '';
  for (const row of rows) {
    const dt = document.createElement('dt');
    dt.textContent = row.label;
    const dd = document.createElement('dd');
    dd.textContent = row.value;
    container.appendChild(dt);
    container.appendChild(dd);
  }
}

/**
 * Pick the Data Basis block for the active population.
 *
 * Falls back to the general `data_basis` block when the gold document has
 * no `data_basis_relevant` key — documents written before FEATURE-016 do
 * not, and the tab must keep rendering against them rather than go blank.
 *
 * @returns {object|null|undefined}
 */
function activeDataBasisBlock() {
  if (activePopulation === 'relevant' && cachedData.data_basis_relevant) {
    return cachedData.data_basis_relevant;
  }
  return cachedData.data_basis;
}

/**
 * Render every Data Basis chart plus the search-config panel.
 *
 * All charts read the block of the active population, narrowed by the
 * current district/neighbourhood scope. The search-config panel is static
 * and always reads `data_basis`.
 *
 * @param {{viewport: string, colorScheme: string}} context
 * @param {{initial: boolean}} [options]
 */
async function renderDataBasisTab(context, { initial = true } = {}) {
  renderSearchConfigPanel();
  const scopedBlock = applyScope(activeDataBasisBlock());
  for (const renderer of DATA_BASIS_RENDERERS) {
    await plotChart(renderer, scopedBlock, context, { initial });
  }
}

/**
 * Call Plotly.Plots.resize on every Data Basis chart container. Used when
 * re-activating a tab whose charts were already rendered while hidden (or
 * whose viewport changed while the tab was inactive) — Plotly needs an
 * explicit nudge to pick up the container's now-visible dimensions.
 */
function resizeDataBasisCharts() {
  for (const renderer of DATA_BASIS_RENDERERS) {
    const container = containers[renderer.id];
    if (container) globalThis.Plotly.Plots.resize(container);
  }
}

/**
 * Nudge each pipeline health detail chart to pick up its container's
 * now-visible dimensions after the tab is re-activated (mirrors
 * resizeDataBasisCharts() above).
 */
function resizePipelineHealthCharts() {
  for (const { renderer } of PIPELINE_HEALTH_CHARTS) {
    const container = document.getElementById(renderer.id);
    if (container) globalThis.Plotly.Plots.resize(container);
  }
}

/**
 * Render the Pipeline Health tab: fetch its independent JSON (lazily, once)
 * and render the overall badge, one row per sub-light check, the Medallion
 * diagram, the 4 detail charts and their threshold captions — or the
 * neutral "not yet available" message on any load failure.
 *
 * Never throws — PipelineHealthDataSource.loadOrUnavailable() already
 * resolves to null on any failure, and this function renders the
 * unavailable state for that case rather than propagating. A null document
 * never calls into Plotly at all (task 13.12 acceptance criterion).
 *
 * @param {{initial?: boolean}} [options] - initial=true uses Plotly.newPlot
 *   for the detail charts (first render); false uses Plotly.react so a
 *   locale/theme change re-renders efficiently without refetching.
 */
async function renderPipelineHealthTab({ initial = true } = {}) {
  if (pipelineHealthDocument === null) {
    pipelineHealthDocument = await pipelineHealthDataSource.loadOrUnavailable();
  }

  const overallEl = document.getElementById('pipeline-health-overall');
  const sublightsEl = document.getElementById('pipeline-health-sublights');
  const unavailableEl = document.getElementById('pipeline-health-unavailable');
  const diagramEl = document.getElementById('pipeline-health-diagram');
  if (!overallEl || !sublightsEl || !unavailableEl) return;

  if (!pipelineHealthDocument) {
    overallEl.hidden = true;
    sublightsEl.hidden = true;
    unavailableEl.hidden = false;
    unavailableEl.textContent = unavailableMessage(currentLocale);
    if (diagramEl) diagramEl.innerHTML = '';
    for (const { renderer } of PIPELINE_HEALTH_CHARTS) {
      const container = document.getElementById(renderer.id);
      if (container) container.innerHTML = '';
    }
    return;
  }

  overallEl.hidden = false;
  sublightsEl.hidden = false;
  unavailableEl.hidden = true;

  overallEl.setAttribute('data-status', pipelineHealthDocument.overall_status);
  overallEl.textContent = overallBadgeLabel(pipelineHealthDocument.overall_status, currentLocale);

  sublightsEl.textContent = '';
  for (const row of buildSubLightRows(pipelineHealthDocument, currentLocale)) {
    const li = document.createElement('li');
    li.className = 'pipeline-health-sublight';
    li.setAttribute('data-status', pipelineHealthDocument[row.id]?.status ?? '');

    const dot = document.createElement('span');
    dot.className = 'pipeline-health-sublight-dot';
    dot.setAttribute('aria-hidden', 'true');

    const label = document.createElement('span');
    label.className = 'pipeline-health-sublight-label';
    label.textContent = `${row.label}: ${row.statusLabel}`;

    const summary = document.createElement('span');
    summary.className = 'pipeline-health-sublight-summary';
    summary.textContent = row.summary;

    li.append(dot, label, summary);
    sublightsEl.appendChild(li);
  }

  // Medallion pipeline diagram (task 13.11/13.12) — pure model + SVG string,
  // safe to inject even for a v1.0 fixture missing per-function detail
  // (buildDiagramModel degrades missing functions to an 'unknown' status).
  if (diagramEl) {
    const model = buildDiagramModel(pipelineHealthDocument);
    diagramEl.innerHTML = renderDiagramSvg(model, currentLocale);
  }

  // 4 detail charts + threshold captions (task 13.6/13.7-13.10/13.12) — the
  // chart-data helpers are null-safe, so a v1.0 fixture (no recent_invocations/
  // monthly_cost_by_service yet) simply renders an empty/neutral chart
  // instead of throwing.
  for (const { renderer, titleKey, checkId } of PIPELINE_HEALTH_CHARTS) {
    const captionEl = document.getElementById(`pipeline-health-threshold-${checkId}`);
    if (captionEl) captionEl.textContent = thresholdRuleText(checkId, currentLocale);

    const container = document.getElementById(renderer.id);
    if (!container) continue;

    const fig = renderer.render(pipelineHealthDocument, currentContext);
    fig.layout.title = { text: t(currentLocale, titleKey) };

    try {
      if (initial) {
        await globalThis.Plotly.newPlot(container, fig.data, fig.layout, PLOTLY_CONFIG);
      } else {
        await globalThis.Plotly.react(container, fig.data, fig.layout, PLOTLY_CONFIG);
      }
    } catch (err) {
      console.error(`[Dashboard] Failed to render pipeline health chart '${renderer.id}':`, err);
    }
  }
}

const tabButtons = Array.from(document.querySelectorAll('.tab-button'));
const tabPanels = Array.from(document.querySelectorAll('.tab-panel'));

// Tracks the currently active tab id so run() can lazily render the Data
// Basis tab if the page loaded deep-linked to it (i.e. activateTab() ran
// before cachedData existed).
let activeTabId = resolveActiveTab(window.location.hash);

/**
 * Activate a dashboard tab: update aria-selected/tabindex on every tab
 * button, toggle `hidden` on every tabpanel, and (for 'data-basis') lazily
 * render its charts on first activation or resize them if already rendered.
 *
 * @param {string} tabId - A valid tab_state.js tab id.
 * @param {{updateHash?: boolean}} [options] - Set updateHash: false when
 *   reacting to a hashchange event that already updated location.hash (avoids
 *   a redundant/duplicate history entry).
 */
function activateTab(tabId, { updateHash = true } = {}) {
  activeTabId = tabId;
  for (const button of tabButtons) {
    const isActive = button.dataset.tabId === tabId;
    button.setAttribute('aria-selected', String(isActive));
    button.tabIndex = isActive ? 0 : -1;
  }
  for (const panel of tabPanels) {
    panel.hidden = panel.id !== `panel-${tabId}`;
  }
  // Shared filter-bar (FEATURE-014, task 14.13) — visible on Trend Analysis
  // and Data Basis (both consume the same district/neighbourhood/population
  // selection state), hidden only on Pipeline Health, which has no filters.
  const filterBarEl = document.getElementById('filter-bar');
  if (filterBarEl) filterBarEl.hidden = tabId === 'pipeline-health';
  if (updateHash) {
    window.location.hash = buildTabHash(tabId);
  }
  if (tabId === 'data-basis' && cachedData) {
    if (!renderedTabs.has('data-basis')) {
      renderDataBasisTab(currentContext, { initial: true }).then(() => renderedTabs.add('data-basis'));
    } else {
      resizeDataBasisCharts();
    }
  }
  // Pipeline Health is independent of cachedData/dataSource entirely (it
  // has its own DataSource) — it is loaded/rendered on its own first
  // activation, regardless of whether the main gold data has loaded yet.
  if (tabId === 'pipeline-health') {
    if (!renderedTabs.has('pipeline-health')) {
      renderPipelineHealthTab({ initial: true }).then(() => renderedTabs.add('pipeline-health'));
    } else {
      resizePipelineHealthCharts();
    }
  }
}

for (const button of tabButtons) {
  button.addEventListener('click', () => activateTab(button.dataset.tabId));
}

window.addEventListener('hashchange', () => {
  activateTab(resolveActiveTab(window.location.hash), { updateHash: false });
});

// Deep-link to whichever tab the starting hash resolves to (falls back to
// DEFAULT_TAB_ID for a missing/invalid hash) before the first data load, so
// a shared/bookmarked '#data-basis' URL opens directly on that tab.
activateTab(resolveActiveTab(window.location.hash), { updateHash: false });

/**
 * Merge a partial context change, apply the resulting theme, and re-render
 * charts only when dashboard_state.shouldRerender says the change is
 * significant (viewport bucket or color scheme actually changed).
 *
 * @param {{viewport?: string, colorScheme?: string}} partial
 */
function updateContext(partial) {
  const next = { ...currentContext, ...partial };
  const rerenderNeeded = shouldRerender(currentContext, next);
  currentContext = next;
  applyTheme(currentContext.colorScheme);
  if (rerenderNeeded && cachedData) {
    renderAllCharts(currentContext, { initial: false });
    if (renderedTabs.has('data-basis')) {
      renderDataBasisTab(currentContext, { initial: false });
    }
  }
  if (rerenderNeeded && renderedTabs.has('pipeline-health')) {
    renderPipelineHealthTab({ initial: false });
  }
}

/**
 * Explicitly set the theme (from a user-driven toggle), persisting the
 * choice so it survives reloads and takes precedence over the system
 * preference.
 *
 * @param {'light'|'dark'} nextTheme
 */
function setExplicitTheme(nextTheme) {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
  } catch {
    // Storage unavailable — the choice just won't persist across reloads.
  }
  updateContext({ colorScheme: nextTheme });
}

/**
 * Explicitly set the UI locale, persisting the choice so it survives
 * reloads, then apply translations and unconditionally re-render every
 * chart (unlike viewport/theme changes, a locale switch always changes
 * visible text — there is no "insignificant" locale change to filter out).
 *
 * @param {string} nextLocale - One of SUPPORTED_LOCALES.
 */
function setLocale(nextLocale) {
  currentLocale = resolveLocale(nextLocale);
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, currentLocale);
  } catch {
    // Storage unavailable — the choice just won't persist across reloads.
  }
  applyTranslations(currentLocale);
  if (cachedData) {
    const relevantLabelEl = document.getElementById('relevant-label');
    if (relevantLabelEl && cachedData.relevant_filter) {
      relevantLabelEl.textContent = buildRelevantLabel(cachedData.relevant_filter);
    }
    renderScopeFilters();
    renderKpis(applyScope(cachedData[activePopulation]));
    renderAllCharts(currentContext, { initial: false });
    if (renderedTabs.has('data-basis')) {
      renderDataBasisTab(currentContext, { initial: false });
    }
  }
  if (renderedTabs.has('pipeline-health')) {
    renderPipelineHealthTab({ initial: false });
  }
}

// Language dropdown — one radio per SUPPORTED_LOCALES entry (see index.html).
// Delegated 'change' listener handles all five without per-radio wiring.
document.querySelector('#language-menu fieldset')?.addEventListener('change', (e) => {
  if (e.target.name !== 'language') return;
  setLocale(e.target.value);
  document.getElementById('language-menu')?.removeAttribute('open');
});

// Optional theme-toggle control — markup is added by a follow-up task; wiring
// the behaviour here means the button works the moment it exists in the DOM.
document.getElementById('theme-toggle')?.addEventListener('click', () => {
  setExplicitTheme(currentContext.colorScheme === 'dark' ? 'light' : 'dark');
});

// Debounced resize listener — only recompute/rerender on an actual viewport
// bucket change (mobile <-> desktop), never on every resize pixel.
let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    updateContext({ viewport: resolveViewport(window.innerWidth) });
  }, RESIZE_DEBOUNCE_MS);
});

/**
 * (Re)build the checkbox list inside a scope-filter <fieldset>, replacing any
 * previously rendered options/labels. The fieldset's <legend> (accessible
 * name) is preserved.
 *
 * @param {string} axis - 'districts' or 'neighborhoods' — matches the
 *   fieldset's [data-scope-options] attribute.
 * @param {string[]} options - Available values for this axis.
 * @param {string[]} selected - Currently selected values for this axis.
 */
function renderScopeOptions(axis, options, selected) {
  const fieldset = document.querySelector(`[data-scope-options="${axis}"]`);
  if (!fieldset) return;

  // Remove previously rendered labels/empty-state, keep the <legend>.
  fieldset.querySelectorAll('label, .scope-empty').forEach((el) => el.remove());

  if (options.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'scope-empty';
    empty.textContent = t(currentLocale, 'filters.noDataAvailable');
    fieldset.appendChild(empty);
    return;
  }

  const atCap = selected.length >= MAX_SCOPE_SELECTION;
  for (const value of options) {
    const isChecked = selected.includes(value);
    const label = document.createElement('label');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = value;
    checkbox.checked = isChecked;
    checkbox.disabled = atCap && !isChecked;
    const span = document.createElement('span');
    span.textContent = value;
    label.append(checkbox, span);
    fieldset.appendChild(label);
  }
}

/**
 * Update a scope dropdown's summary badge to reflect its current selection
 * count (or 'All' when nothing is selected).
 *
 * @param {string} axis - 'districts' or 'neighborhoods'.
 * @param {string[]} selected
 */
function updateScopeBadge(axis, selected) {
  const badge = document.querySelector(`[data-scope-count="${axis}"]`);
  if (!badge) return;
  badge.textContent = selected.length > 0 ? `${selected.length}/${MAX_SCOPE_SELECTION}` : t(currentLocale, 'filters.badgeAll');
  badge.toggleAttribute('data-active', selected.length > 0);
}

/**
 * Rebuild both scope-filter checkbox lists and badges from the current
 * selection state, and prune any selected neighborhoods that fall outside
 * the districts currently selected (avoids a silently-ignored stale
 * selection biasing the filtered data without appearing checked anywhere).
 *
 * District/neighborhood *options* are always derived from the unfiltered
 * 'general' population block, so the available choices stay stable
 * regardless of the population toggle.
 */
function renderScopeFilters() {
  const districtOptions = extractDistricts(cachedData.general);
  const neighborhoodOptions = extractNeighborhoods(cachedData.general, selectedDistricts);
  selectedNeighborhoods = selectedNeighborhoods.filter((n) => neighborhoodOptions.includes(n));

  renderScopeOptions('districts', districtOptions, selectedDistricts);
  renderScopeOptions('neighborhoods', neighborhoodOptions, selectedNeighborhoods);
  updateScopeBadge('districts', selectedDistricts);
  updateScopeBadge('neighborhoods', selectedNeighborhoods);

  const resetBtn = document.getElementById('scope-reset');
  if (resetBtn) resetBtn.hidden = selectedDistricts.length === 0 && selectedNeighborhoods.length === 0;
}

/**
 * React to a district/neighborhood scope change: rebuild the filter UI and
 * re-render the KPI row + every chart against the newly scoped data.
 *
 * Re-renders whichever tab(s) are relevant: Trend Analysis always (its KPIs/
 * charts are always scope-aware), and Data Basis too if it has already been
 * rendered at least once (FEATURE-014, task 14.13) — the shared filter-bar
 * now sits above both tabs, so a scope change while on Data Basis must
 * refresh all of its charts.
 */
async function onScopeChange() {
  renderScopeFilters();
  if (!cachedData) return;
  renderKpis(applyScope(cachedData[activePopulation]));
  await renderAllCharts(currentContext, { initial: false });
  if (renderedTabs.has('data-basis')) {
    await renderDataBasisTab(currentContext, { initial: false });
  }
}

// Event delegation: one listener per fieldset handles all its checkboxes,
// including ones re-created by renderScopeOptions() on every change.
document.querySelector('[data-scope-options="districts"]')?.addEventListener('change', (e) => {
  if (e.target.type !== 'checkbox') return;
  selectedDistricts = toggleScopeSelection(selectedDistricts, e.target.value);
  onScopeChange().catch((err) => console.error('[Dashboard] Scope change failed:', err));
});

document.querySelector('[data-scope-options="neighborhoods"]')?.addEventListener('change', (e) => {
  if (e.target.type !== 'checkbox') return;
  selectedNeighborhoods = toggleScopeSelection(selectedNeighborhoods, e.target.value);
  onScopeChange().catch((err) => console.error('[Dashboard] Scope change failed:', err));
});

document.getElementById('scope-reset')?.addEventListener('click', () => {
  selectedDistricts = [];
  selectedNeighborhoods = [];
  onScopeChange().catch((err) => console.error('[Dashboard] Scope reset failed:', err));
});

// NOTE: no longer auto-following OS color-scheme changes — the dashboard
// defaults to dark (DEFAULT_COLOR_SCHEME) regardless of the OS preference,
// and only an explicit in-page toggle click (setExplicitTheme) changes it.

/**
 * Load the gold data once, then render the KPI row and every chart.
 * General-only renderers always receive data.general; toggle renderers
 * receive the active population. On failure, shows the retry block.
 */
async function run() {
  setLoadState(createLoadState());

  try {
    cachedData = await dataSource.load();
  } catch (err) {
    console.error('[Dashboard] Failed to load data:', err);
    setLoadState(transition(loadState, { type: 'error' }));
    return;
  }
  setLoadState(transition(loadState, { type: 'success' }));

  // Update the toggle label to show the actual filter criteria.
  const relevantLabelEl = document.getElementById('relevant-label');
  if (relevantLabelEl && cachedData.relevant_filter) {
    relevantLabelEl.textContent = buildRelevantLabel(cachedData.relevant_filter);
  }

  renderScopeFilters();
  renderKpis(applyScope(cachedData.general));
  applyTheme(currentContext.colorScheme);
  await renderAllCharts(currentContext, { initial: true });

  // If the page loaded deep-linked to the Data Basis tab (activateTab() ran
  // before cachedData existed, so its lazy-render branch was a no-op then),
  // render it now that data is available.
  if (activeTabId === 'data-basis' && !renderedTabs.has('data-basis')) {
    await renderDataBasisTab(currentContext, { initial: true });
    renderedTabs.add('data-basis');
  }

  // Show the toggle only when 'relevant' data is present. Wire the change
  // listener once (a retry-triggered re-run of run() must not attach a
  // second listener that would double-fire chart updates on every toggle).
  const toggleEl = document.getElementById('population-toggle');
  if (toggleEl && cachedData.relevant) {
    toggleEl.hidden = false;
    if (!toggleEl.dataset.wired) {
      toggleEl.dataset.wired = 'true';
      toggleEl.addEventListener('change', async (e) => {
        activePopulation = e.target.value;
        const activeScoped = applyScope(cachedData[activePopulation]);
        // Update the KPI row immediately too, so the toggle gives visible
        // feedback even before scrolling down to the population-specific charts.
        renderKpis(activeScoped);
        for (const renderer of TOGGLE_RENDERERS) {
          await plotChart(renderer, activeScoped, currentContext, { initial: false });
        }
        // Data Basis has its own per-population block; refresh it only if it
        // has been rendered (a first visit picks up activePopulation itself).
        if (renderedTabs.has('data-basis')) {
          await renderDataBasisTab(currentContext, { initial: false });
        }
      });
    }
  }
}

document.getElementById('retry-button')?.addEventListener('click', () => {
  run().catch((err) => console.error('[Dashboard] Retry failed:', err));
});

// Reflect the resolved starting locale (stored choice or DEFAULT_LOCALE) on
// the DOM before the first data load, so static chrome (header, error copy,
// KPI labels, filter labels) is never shown in the wrong language even
// briefly while data.load() is in flight.
document.querySelectorAll('#language-menu input[name="language"]').forEach((el) => {
  el.checked = el.value === currentLocale;
});
applyTranslations(currentLocale);

run().catch((err) => {
  console.error('[Dashboard] Failed to render:', err);
});
