"use strict";

const CATEGORY_LABELS = {
  overview: "Overview",
  zprofile: "Z-axis profile",
  calibration: "Calibration",
  segmentation: "Segmentation",
  objects: "Objects",
};

const Z_METRICS = {
  mean: "Mean intensity",
  max: "Maximum intensity",
  std: "Standard deviation",
  focus: "Focus (Laplacian variance)",
  foregroundPercent: "Foreground %",
  snr: "Signal-to-noise ratio",
};

const byId = (id) => document.getElementById(id);
let bridge = null;
let appSettings = null;
let currentStack = null;
let currentAnalysis = null;
let visibleCategories = [];
let activeCategory = "overview";
let activeZMetric = "mean";
let currentSlice = 0;
// Redraw callbacks for charts/tables that show the current slice.
let sliceListeners = [];
let toastTimer = 0;

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== "") element.textContent = String(text);
  return element;
}

function invoke(method, ...args) {
  return new Promise((resolve, reject) => {
    if (!bridge || typeof bridge[method] !== "function") {
      reject(new Error("The Python application bridge is not ready."));
      return;
    }
    bridge[method](...args, (raw) => {
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error("The application returned an invalid response."));
      }
    });
  });
}

function setStatus(message, kind = "") {
  const status = byId("appStatus");
  status.textContent = message;
  status.className = `status-line${kind ? ` ${kind}` : ""}`;
}

function showToast(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 3300);
}

function applyTheme(mode, effectiveTheme) {
  byId("themeSelect").value = mode;
  document.documentElement.dataset.theme = effectiveTheme;
  if (appSettings) {
    document.documentElement.style.fontSize = `${(appSettings.fontSize * 4) / 3}px`;
  }
}

function setBusy(button, busy, text) {
  if (!button.dataset.defaultLabel) button.dataset.defaultLabel = button.textContent.trim();
  button.disabled = busy;
  button.textContent = busy ? text : button.dataset.defaultLabel;
}

function formatNumber(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { maximumSignificantDigits: digits });
}

const compactFormat = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 2,
});

function unit() {
  return currentAnalysis?.calibration.unit || currentStack?.calibration.unit || "µm";
}

function withUnit(value, power = 1, digits = 4) {
  if (value === null || value === undefined) return "—";
  const suffix = power === 3 ? "³" : power === 2 ? "²" : "";
  return `${formatNumber(value, digits)} ${unit()}${suffix}`;
}

function setView(view) {
  const onResults = view === "results" && currentAnalysis !== null;
  byId("inputView").hidden = onResults;
  byId("resultsView").hidden = !onResults;
  byId("mainNav").classList.toggle("active", !onResults);
  byId("mainNav").toggleAttribute("aria-current", !onResults);
  byId("dataNav").classList.toggle("active", onResults);
  byId("dataNav").toggleAttribute("aria-current", onResults);
  byId("dataNav").disabled = currentAnalysis === null;
  byId("searchControl").hidden = !onResults;
  byId("searchLabel").hidden = !onResults;
  byId("exportButton").disabled = currentAnalysis === null;
  byId("exportCsvButton").disabled = currentAnalysis === null;
  byId("pageTitle").textContent = onResults ? "Analysis data" : "Stack Analysis Studio";
  byId("pageEyebrow").textContent = onResults ? "RESULTS WORKSPACE" : "WORKSPACE";
  if (!onResults) {
    byId("globalSearch").value = "";
    byId("searchResults").hidden = true;
    byId("resultsContent").hidden = false;
    byId("categoryTabs").hidden = false;
  }
}

function getCategories() {
  return Object.keys(CATEGORY_LABELS).filter(
    (key) => key === "overview" || appSettings.categories[key] !== false,
  );
}

function parameterTooltip(label) {
  const lower = String(label).trim().toLocaleLowerCase().replace(/\s*\(.*\)$/, "");
  const definitions = {
    "objects": "Number of 3D objects after thresholding, size filtering and (optionally) watershed splitting.",
    "object count": "Number of 3D objects after thresholding, size filtering and (optionally) watershed splitting.",
    "total object volume": "Sum of all object volumes: voxel count × voxel volume.",
    "total volume": "Sum of all object volumes: voxel count × voxel volume.",
    "mean equivalent diameter": "Diameter of a sphere with the same volume as the object, averaged over objects.",
    "equivalent diameter": "Diameter of a sphere with the same volume as the object.",
    "brightest slice": "Slice with the highest mean intensity.",
    "best-focus slice": "Slice with the highest variance of the Laplacian (sharpest edges). Noise also raises this value.",
    "half-intensity depth": "Distance from the brightest slice to the first deeper slice whose mean drops below half-way between the dimmest and brightest slice means.",
    "focus": "Variance of the 2D Laplacian of the slice. Relative only; it also rises with noise.",
    "snr": "(mean foreground − mean background) / background standard deviation, using the stack-wide Otsu threshold.",
    "signal-to-noise ratio": "(mean foreground − mean background) / background standard deviation, using the stack-wide Otsu threshold.",
    "foreground %": "Percentage of the slice above the stack-wide Otsu threshold.",
    "anisotropy": "Voxel z size divided by the finest lateral size. Values above 1 mean z is sampled more coarsely.",
    "voxel volume": "z × y × x voxel size.",
    "scale bar length": "Scale bar length chosen as 1, 2 or 5 × 10ⁿ, about a fifth of the image width.",
    "sphericity": "π^(1/3)·(6V)^(2/3) / A. Equal to 1 for a perfect sphere; lower for irregular shapes.",
    "surface area": "Marching-cubes mesh area on a slightly blurred mask, in physical units.",
    "volume": "Voxel count × voxel volume.",
    "z extent": "Number of slices the object spans × voxel z size.",
    "mean intensity": "Mean raw intensity of the object's voxels in the analysed channel.",
    "threshold method": "Otsu and Li choose a threshold automatically from the smoothed image; manual uses your value.",
    "gaussian sigma": "Smoothing width in physical units, converted per axis to voxels.",
    "minimum object volume": "Objects smaller than this are removed, before and after splitting.",
    "watershed minimum distance": "Minimum physical distance between object centres when splitting touching objects.",
    "removed as too small": "Number of connected components or fragments removed by the minimum-volume filter.",
  };
  return definitions[lower] || "";
}

function addParameterTooltip(element, label) {
  const explanation = parameterTooltip(label);
  if (explanation) element.title = explanation;
  return element;
}

function goToSlice(index) {
  if (index === null || index === undefined) return;
  invoke("goToSlice", Number(index))
    .then((result) => {
      if (!result.ok) showToast(result.error, true);
    })
    .catch((error) => showToast(error.message, true));
}

function createTable(headers, rows, numericColumns = [], category = activeCategory, targetPrefix = "row", options = {}) {
  const wrap = node("div", "table-wrap");
  const table = node("table", "data-table");
  if (options.className) table.classList.add(options.className);
  const thead = node("thead");
  const headerRow = node("tr");
  headers.forEach((header, index) => {
    const th = node("th", numericColumns.includes(index) ? "numeric" : "", header);
    addParameterTooltip(th, header);
    headerRow.append(th);
  });
  thead.append(headerRow);
  const tbody = node("tbody");
  const slices = options.rowSlices || [];
  rows.forEach((row, index) => {
    const tr = node("tr");
    tr.id = `search-target-${category}-${targetPrefix}-${index}`;
    tr.tabIndex = -1;
    row.forEach((value, column) => {
      const td = node("td", numericColumns.includes(column) ? "numeric" : "", value);
      if (column === 0) addParameterTooltip(td, value);
      tr.append(td);
    });
    const slice = slices[index];
    if (slice !== null && slice !== undefined) {
      tr.dataset.slice = String(slice);
      tr.classList.add("slice-row");
      tr.tabIndex = 0;
      tr.title = `Show slice ${slice} in napari`;
      tr.addEventListener("click", () => goToSlice(slice));
      tr.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          goToSlice(slice);
        }
      });
    }
    tbody.append(tr);
  });
  table.append(thead, tbody);
  wrap.append(table);
  if (options.highlightSlice) {
    const highlight = () => {
      tbody.querySelectorAll("tr[data-slice]").forEach((tr) => {
        tr.classList.toggle("current-slice", Number(tr.dataset.slice) === currentSlice);
      });
    };
    highlight();
    sliceListeners.push(highlight);
  }
  return wrap;
}

function referenceHeading(title, reference, tagName = "h3") {
  const heading = node(tagName);
  addParameterTooltip(heading, title);
  if (appSettings?.enableReferenceLinks && reference) {
    const link = node("a", "reference-link", title);
    addParameterTooltip(link, title);
    link.href = "#";
    link.dataset.reference = reference;
    heading.append(link);
  } else {
    heading.textContent = title;
  }
  return heading;
}

function block(title, description = "", reference = "") {
  const section = node("section", "panel-block");
  if (title) section.append(referenceHeading(title, reference));
  if (description) section.append(node("p", "muted-copy", description));
  return section;
}

function panelHeading(title, description, reference = "", extra = null) {
  const heading = node("div", "panel-heading");
  const titleBlock = node("div");
  titleBlock.append(referenceHeading(title, reference));
  titleBlock.append(node("p", "", description));
  heading.append(titleBlock);
  if (extra) heading.append(extra);
  return heading;
}

function renderBarChart(items, chartClass = "") {
  const scroller = node("div", "bar-chart-scroll");
  const chart = node("div", "composition-chart");
  if (chartClass) chart.classList.add(chartClass);
  chart.style.setProperty("--bar-count", String(Math.max(items.length, 1)));
  const maxPercent = Math.max(1, ...items.map((item) => item.percent));
  items.forEach((item) => {
    const column = node("article", "bar-column");
    if (item.id) {
      column.id = item.id;
      column.tabIndex = -1;
    }
    const track = node("div", "bar-track");
    const fill = node("div", "bar-fill");
    fill.style.height = `${Math.max(2, (item.percent / maxPercent) * 100)}%`;
    fill.style.setProperty("--bar-color", item.color || "var(--accent)");
    fill.title = item.tooltip || `${item.label}: ${item.count} (${item.percent.toFixed(1)}%)`;
    track.append(fill);
    column.append(
      track,
      node("strong", "bar-label", item.label),
      node("span", "bar-count", item.countLabel || String(item.count)),
      node("span", "bar-percent", `${item.percent.toFixed(1)}%`),
    );
    chart.append(column);
  });
  scroller.append(chart);
  return scroller;
}

// Line chart with drag-to-zoom (from the reference app), plus a current-slice
// marker and click-to-select: a click without dragging calls options.onSelect.
function renderLineChart(points, title, targetId, axisLabel, reference = "", options = {}) {
  const card = node("div", "chart-card");
  if (targetId) {
    card.id = targetId;
    card.tabIndex = -1;
  }
  card.append(referenceHeading(title, reference));
  if (!points || !points.length) {
    card.append(node("div", "empty-state", "No data to draw this profile."));
    return card;
  }
  const controls = node("div", "chart-controls");
  const resetButton = node("button", "button button-quiet", "Reset zoom");
  resetButton.type = "button";
  resetButton.disabled = true;
  controls.append(
    resetButton,
    node("span", "zoom-hint", options.onSelect ? "Click a point to show that slice · drag to zoom" : "Drag across the graph to zoom in"),
  );
  card.append(controls);
  const svgNs = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNs, "svg");
  svg.setAttribute("viewBox", "0 0 800 310");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", title);
  svg.classList.add("chart-svg", "is-zoomable");
  const left = 66;
  const right = 780;
  const top = 22;
  const bottom = 258;
  card.append(svg);
  const tooltip = node("div", "chart-tooltip");
  tooltip.hidden = true;
  card.append(tooltip);
  let startIndex = 0;
  let endIndex = points.length - 1;
  let dragStart = null;
  let selection = null;

  function localX(event) {
    const bounds = svg.getBoundingClientRect();
    const scale = Math.min(bounds.width / 800, bounds.height / 310);
    const inset = (bounds.width - 800 * scale) / 2;
    return (event.clientX - bounds.left - inset) / scale;
  }

  function xFor(index) {
    const count = endIndex - startIndex;
    return count === 0 ? (left + right) / 2 : left + ((index - startIndex) / count) * (right - left);
  }

  function indexAt(x) {
    return Math.min(endIndex, Math.max(startIndex, Math.round(
      ((x - left) / (right - left)) * (endIndex - startIndex),
    ) + startIndex));
  }

  function appendText(x, y, text, anchor, className = "chart-axis-label") {
    const label = document.createElementNS(svgNs, "text");
    label.setAttribute("x", String(x));
    label.setAttribute("y", String(y));
    label.setAttribute("text-anchor", anchor);
    label.classList.add(className);
    label.textContent = String(text);
    svg.append(label);
  }

  function draw() {
    svg.replaceChildren();
    const visible = points.slice(startIndex, endIndex + 1);
    const values = visible.map((point) => point.value).filter((value) => value !== null).map(Number);
    const min = Math.min(...values, 0);
    const max = Math.max(...values, 0);
    const range = Math.max(max - min, 1e-9);
    for (let index = 0; index <= 4; index += 1) {
      const ratio = index / 4;
      const y = top + (bottom - top) * ratio;
      const line = document.createElementNS(svgNs, "line");
      line.setAttribute("x1", String(left));
      line.setAttribute("x2", String(right));
      line.setAttribute("y1", String(y));
      line.setAttribute("y2", String(y));
      line.classList.add("chart-gridline");
      svg.append(line);
      appendText(left - 9, y + 4, compactFormat.format(max - range * ratio), "end");
    }
    const yFor = (value) => bottom - ((Number(value) - min) / range) * (bottom - top);
    let pathData = "";
    let penDown = false;
    visible.forEach((point, offset) => {
      if (point.value === null) {
        penDown = false;
        return;
      }
      const x = xFor(startIndex + offset);
      pathData += `${penDown ? "L" : "M"} ${x.toFixed(2)} ${yFor(point.value).toFixed(2)} `;
      penDown = true;
    });
    const path = document.createElementNS(svgNs, "path");
    path.setAttribute("d", pathData.trim());
    path.classList.add("chart-line");
    svg.append(path);
    if (visible.length <= 80) {
      visible.forEach((point, offset) => {
        if (point.value === null) return;
        const dot = document.createElementNS(svgNs, "circle");
        dot.setAttribute("cx", String(xFor(startIndex + offset)));
        dot.setAttribute("cy", String(yFor(point.value)));
        dot.setAttribute("r", "3");
        dot.classList.add("chart-dot");
        svg.append(dot);
      });
    }
    // Current-slice marker.
    const markerIndex = points.findIndex((point) => point.slice === currentSlice);
    if (markerIndex >= startIndex && markerIndex <= endIndex) {
      const x = xFor(markerIndex);
      const marker = document.createElementNS(svgNs, "line");
      marker.setAttribute("x1", String(x));
      marker.setAttribute("x2", String(x));
      marker.setAttribute("y1", String(top));
      marker.setAttribute("y2", String(bottom));
      marker.classList.add("chart-slice-marker");
      svg.append(marker);
      appendText(x, top - 7, `slice ${currentSlice}`, "middle", "chart-marker-label");
    }
    appendText(left, 278, visible[0].position, "start");
    appendText(right, 278, visible.at(-1).position, "end");
    appendText((left + right) / 2, 302, axisLabel, "middle");
    selection = document.createElementNS(svgNs, "rect");
    selection.classList.add("chart-selection");
    selection.setAttribute("y", String(top));
    selection.setAttribute("height", String(bottom - top));
    selection.setAttribute("visibility", "hidden");
    svg.append(selection);
    resetButton.disabled = startIndex === 0 && endIndex === points.length - 1;
  }

  svg.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const x = localX(event);
    if (x < left || x > right) return;
    dragStart = Math.max(left, Math.min(right, x));
    svg.setPointerCapture(event.pointerId);
    tooltip.hidden = true;
  });
  svg.addEventListener("pointermove", (event) => {
    const rawX = localX(event);
    if (dragStart !== null) {
      const x = Math.max(left, Math.min(right, rawX));
      selection.setAttribute("x", String(Math.min(dragStart, x)));
      selection.setAttribute("width", String(Math.abs(x - dragStart)));
      selection.setAttribute("visibility", "visible");
      return;
    }
    if (rawX < left || rawX > right) {
      tooltip.hidden = true;
      return;
    }
    tooltip.textContent = points[indexAt(rawX)].tooltip;
    tooltip.hidden = false;
    const cardBounds = card.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(
      event.clientX - cardBounds.left + 12,
      cardBounds.width - tooltip.offsetWidth - 8,
    ))}px`;
    tooltip.style.top = `${Math.max(8, event.clientY - cardBounds.top - tooltip.offsetHeight - 12)}px`;
  });
  svg.addEventListener("pointerup", (event) => {
    if (dragStart === null) return;
    const finish = Math.max(left, Math.min(right, localX(event)));
    const initial = dragStart;
    dragStart = null;
    selection.setAttribute("visibility", "hidden");
    if (Math.abs(finish - initial) < 18) {
      // A click rather than a drag: select the nearest point.
      if (options.onSelect) options.onSelect(points[indexAt(finish)]);
      return;
    }
    const first = indexAt(Math.min(initial, finish));
    const last = indexAt(Math.max(initial, finish));
    if (last - first < 1) return;
    startIndex = first;
    endIndex = last;
    tooltip.hidden = true;
    draw();
  });
  svg.addEventListener("pointercancel", () => {
    dragStart = null;
    if (selection) selection.setAttribute("visibility", "hidden");
  });
  svg.addEventListener("pointerleave", () => {
    if (dragStart === null) tooltip.hidden = true;
  });
  resetButton.addEventListener("click", () => {
    startIndex = 0;
    endIndex = points.length - 1;
    tooltip.hidden = true;
    draw();
  });
  draw();
  sliceListeners.push(draw);
  return card;
}

// ---------------------------------------------------------------- row data
// Shared by the renderers and the global search so both use the same targets.

function calibrationRows(data) {
  const cal = data.calibration;
  const u = cal.unit;
  return [
    ["Voxel size z", `${formatNumber(cal.z, 6)} ${u}`],
    ["Voxel size y", `${formatNumber(cal.y, 6)} ${u}`],
    ["Voxel size x", `${formatNumber(cal.x, 6)} ${u}`],
    ["Voxel volume", `${formatNumber(cal.voxelVolume, 5)} ${u}³`],
    ["Anisotropy (z / finest lateral)", formatNumber(cal.anisotropy, 3)],
    ["Stack shape (z × y × x)", cal.shape.join(" × ")],
    ["Physical extent (z × y × x)", `${cal.extent.map((e) => formatNumber(e, 4)).join(" × ")} ${u}`],
    ["Stack volume", `${formatNumber(cal.stackVolume, 5)} ${u}³`],
    ["Scale bar length", `${formatNumber(cal.scaleBarLength)} ${u}`],
  ];
}

function zSummaryRows(data) {
  const profile = data.zProfile;
  const z = data.calibration.z;
  return {
    rows: [
      ["Brightest slice", `${profile.brightestSlice} · z ${formatNumber(profile.brightestSlice * z)} ${unit()}`],
      ["Best-focus slice", `${profile.bestFocusSlice} · z ${formatNumber(profile.bestFocusSlice * z)} ${unit()}`],
      [
        "Half-intensity depth",
        profile.halfIntensityDepth === null
          ? "Not reached in this stack"
          : `${withUnit(profile.halfIntensityDepth)} · reached at slice ${profile.halfIntensitySlice}`,
      ],
      ["Stack Otsu threshold", formatNumber(profile.threshold)],
    ],
    slices: [profile.brightestSlice, profile.bestFocusSlice, profile.halfIntensitySlice, null],
  };
}

function zSliceRows(data) {
  return data.zProfile.slices.map((item) => [
    item.index,
    formatNumber(item.z),
    formatNumber(item.mean),
    formatNumber(item.max),
    formatNumber(item.std),
    formatNumber(item.focus),
    formatNumber(item.foregroundPercent, 3),
    formatNumber(item.snr, 3),
    data.objectsPerSlice[item.index],
  ]);
}

function segmentationParameterRows(data) {
  const settings = data.settings;
  const segmentation = data.segmentation;
  return [
    ["Threshold method", settings.method === "li" ? "Li" : settings.method === "otsu" ? "Otsu" : "Manual"],
    ["Gaussian sigma", `${withUnit(settings.sigma)} · ${segmentation.sigmaVoxels.map((s) => formatNumber(s, 3)).join(" / ")} voxels (z/y/x)`],
    ["Minimum object volume", `${withUnit(settings.minVolume, 3)} · ${segmentation.minVoxels} voxels`],
    ["Split touching objects", settings.splitTouching ? "Yes (watershed on distance transform)" : "No"],
    ["Watershed minimum distance", withUnit(settings.minDistance)],
  ];
}

function segmentationResultRows(data) {
  const segmentation = data.segmentation;
  return [
    ["Threshold value (smoothed image)", formatNumber(segmentation.threshold)],
    ["Objects before splitting", segmentation.objectsBeforeSplit],
    ["Object count", segmentation.objectCount],
    ["Removed as too small", segmentation.removedSmall],
    ["Foreground (final objects)", `${formatNumber(segmentation.foregroundPercent, 3)}%`],
  ];
}

function objectSummaryRows(data) {
  const summary = data.summary;
  return [
    ["Object count", summary.count],
    ["Total volume", withUnit(summary.totalVolume, 3)],
    ["Mean volume", withUnit(summary.meanVolume, 3)],
    ["Median volume", withUnit(summary.medianVolume, 3)],
    ["Smallest / largest volume", `${withUnit(summary.minVolume, 3)} / ${withUnit(summary.maxVolume, 3)}`],
    ["Mean equivalent diameter", withUnit(summary.meanDiameter)],
    ["Mean sphericity", formatNumber(summary.meanSphericity, 3)],
  ];
}

function objectRows(data) {
  return data.objects.map((item) => [
    item.label,
    formatNumber(item.volume),
    formatNumber(item.surfaceArea),
    formatNumber(item.equivalentDiameter),
    formatNumber(item.sphericity, 3),
    `${item.zStart}–${item.zEnd}`,
    formatNumber(item.zExtent),
    `${formatNumber(item.centroidZ)}, ${formatNumber(item.centroidY)}, ${formatNumber(item.centroidX)}`,
    formatNumber(item.meanIntensity),
  ]);
}

// --------------------------------------------------------------- renderers

function renderOverview(data) {
  const panel = node("div", "category-panel");
  panel.append(panelHeading("At a glance", "Key results from the z-profile and 3D segmentation.", "napari"));
  const grid = node("div", "metric-grid");
  const accents = ["#6ea9ff", "#59d9c4", "#bc9aff", "#ffc36f", "#ff8ba5", "#76d99b"];
  data.cards.forEach(([label, value], index) => {
    const card = node("article", "metric-card");
    card.id = `search-target-overview-card-${index}`;
    card.tabIndex = -1;
    card.style.setProperty("--metric-accent", accents[index % accents.length]);
    card.append(addParameterTooltip(node("div", "metric-label", label), label));
    card.append(node("div", "metric-value", value));
    grid.append(card);
  });
  panel.append(grid);
  const stack = block("Stack", "", "cells3d");
  stack.append(createTable(
    ["Property", "Value"],
    [
      ["Sample", data.sampleName],
      ["Source", data.source],
      ["Analysed channel", data.channel],
      ["Shape (z × y × x)", data.calibration.shape.join(" × ")],
      ["Voxel size (z, y, x)", `${[data.calibration.z, data.calibration.y, data.calibration.x].map((v) => formatNumber(v, 6)).join(", ")} ${unit()}`],
    ],
    [],
    "overview",
    "stack",
  ));
  panel.append(stack);
  const profile = block(
    "Mean intensity along z",
    "Click a point to show that slice in napari. The dashed line marks the current slice.",
  );
  profile.append(renderLineChart(
    zPoints(data, "mean"),
    "Mean intensity by slice",
    "search-target-overview-profile",
    `Slice (z step ${formatNumber(data.calibration.z)} ${unit()})`,
    "",
    { onSelect: (point) => goToSlice(point.slice) },
  ));
  panel.append(profile);
  panel.append(node(
    "p",
    "note",
    "All values are estimates from classical image analysis on sample data. The exported Markdown report lists methods and limitations.",
  ));
  return panel;
}

function zPoints(data, metric) {
  const label = Z_METRICS[metric];
  return data.zProfile.slices.map((item) => ({
    slice: item.index,
    position: item.index,
    value: item[metric],
    tooltip: `Slice ${item.index} · z ${formatNumber(item.z)} ${unit()} · ${label} ${formatNumber(item[metric])}`,
  }));
}

function renderZProfile(data) {
  const panel = node("div", "category-panel");
  const metricLabel = node("label", "composition-sort-label", "Metric");
  const metricSelect = node("select", "composition-sort");
  metricSelect.setAttribute("aria-label", "Z-profile metric");
  Object.entries(Z_METRICS).forEach(([value, label]) => {
    const option = node("option", "", label);
    option.value = value;
    metricSelect.append(option);
  });
  metricSelect.value = activeZMetric;
  metricLabel.append(metricSelect);
  panel.append(panelHeading(
    "Z-axis profile",
    "Per-slice intensity and quality metrics. Foreground and SNR use one stack-wide Otsu threshold.",
    "laplace",
    metricLabel,
  ));
  const axis = `Slice (z step ${formatNumber(data.calibration.z)} ${unit()})`;
  const chartHost = node("div");
  const drawMetric = () => {
    // Drop the old chart's slice listener before drawing a new one.
    const previous = chartHost.firstChild?._draw;
    if (previous) sliceListeners = sliceListeners.filter((fn) => fn !== previous);
    const card = renderLineChart(
      zPoints(data, activeZMetric),
      `${Z_METRICS[activeZMetric]} by slice`,
      "search-target-zprofile-profile",
      axis,
      activeZMetric === "focus" ? "laplace" : "",
      { onSelect: (point) => goToSlice(point.slice) },
    );
    card._draw = sliceListeners.at(-1);
    chartHost.replaceChildren(card);
  };
  drawMetric();
  metricSelect.addEventListener("change", () => {
    activeZMetric = metricSelect.value;
    drawMetric();
  });
  panel.append(chartHost);

  const counts = block("Objects per slice", "Number of segmented objects present in each slice.");
  counts.append(renderLineChart(
    data.objectsPerSlice.map((count, index) => ({
      slice: index,
      position: index,
      value: count,
      tooltip: `Slice ${index} · ${count} object${count === 1 ? "" : "s"}`,
    })),
    "Objects per slice",
    "search-target-zprofile-objects",
    axis,
    "",
    { onSelect: (point) => goToSlice(point.slice) },
  ));
  panel.append(counts);

  const summary = zSummaryRows(data);
  const summaryBlock = block("Depth summary", "Click a row to show that slice.");
  summaryBlock.append(createTable(["Property", "Value"], summary.rows, [], "zprofile", "summary", {
    rowSlices: summary.slices,
  }));
  panel.append(summaryBlock);

  const table = block("Per-slice values", "Click a row to show that slice. The current slice is highlighted.");
  table.append(createTable(
    ["Slice", `z (${unit()})`, "Mean", "Max", "Std", "Focus", "Foreground %", "SNR", "Objects"],
    zSliceRows(data),
    [0, 1, 2, 3, 4, 5, 6, 7, 8],
    "zprofile",
    "slice",
    { rowSlices: data.zProfile.slices.map((item) => item.index), highlightSlice: true },
  ));
  panel.append(table);
  return panel;
}

function renderCalibration(data) {
  const panel = node("div", "category-panel");
  panel.append(panelHeading(
    "Calibration",
    "Voxel size used for the scale bar, the z axis, volumes and surface areas.",
    "napari",
  ));
  panel.append(createTable(["Property", "Value"], calibrationRows(data), [], "calibration", "property"));
  const original = currentStack?.originalCalibration;
  if (original) {
    const sample = block("Sample's published voxel size");
    sample.append(createTable(
      ["Axis", "Value"],
      [
        ["z", `${formatNumber(original.z, 6)} ${original.unit}`],
        ["y", `${formatNumber(original.y, 6)} ${original.unit}`],
        ["x", `${formatNumber(original.x, 6)} ${original.unit}`],
      ],
      [],
      "calibration",
      "original",
    ));
    panel.append(sample);
  }
  panel.append(node(
    "p",
    "note",
    "Anisotropy is handled in smoothing (sigma converted per axis), the watershed distance transform (voxel size as sampling) and surface area (marching cubes with voxel spacing). Change the voxel size on the main screen; the analysis is then cleared because physical values would be stale.",
  ));
  return panel;
}

function renderSegmentation(data) {
  const panel = node("div", "category-panel");
  panel.append(panelHeading(
    "3D segmentation",
    "Gaussian smoothing → threshold → 26-connected labels → minimum-volume filter → optional watershed.",
    data.settings.method === "li" ? "li" : data.settings.method === "otsu" ? "otsu" : "watershed",
  ));
  panel.append(createTable(["Parameter", "Value"], segmentationParameterRows(data), [], "segmentation", "parameter"));
  const results = block("Result", "", "watershed");
  results.append(createTable(["Property", "Value"], segmentationResultRows(data), [], "segmentation", "result"));
  panel.append(results);
  const histogram = block("Object volume distribution", `Number of objects per volume range (${unit()}³).`);
  const volumes = data.objects.map((item) => Number(item.volume));
  if (volumes.length) {
    const binCount = Math.min(10, Math.max(1, Math.ceil(Math.sqrt(volumes.length))));
    const low = Math.min(...volumes);
    const high = Math.max(...volumes);
    const width = (high - low) / binCount || 1;
    const bins = Array.from({ length: binCount }, (_, index) => ({ from: low + index * width, count: 0 }));
    volumes.forEach((volume) => {
      bins[Math.min(binCount - 1, Math.floor((volume - low) / width))].count += 1;
    });
    histogram.append(renderBarChart(bins.map((bin) => ({
      label: `${compactFormat.format(bin.from)}–${compactFormat.format(bin.from + width)}`,
      count: bin.count,
      countLabel: `${bin.count} object${bin.count === 1 ? "" : "s"}`,
      percent: (bin.count / volumes.length) * 100,
    }))));
  } else {
    histogram.append(node("div", "empty-state", "No objects were found with these settings."));
  }
  panel.append(histogram);
  return panel;
}

function renderObjects(data) {
  const panel = node("div", "category-panel");
  panel.append(panelHeading(
    "Objects",
    "Per-object measurements in calibrated units. Click a row to show the object's centre slice.",
    "marching-cubes",
  ));
  panel.append(createTable(["Summary", "Value"], objectSummaryRows(data), [], "objects", "summary"));
  const table = block(`${data.objects.length} object${data.objects.length === 1 ? "" : "s"}`);
  if (data.objects.length) {
    table.append(createTable(
      [
        "Label",
        `Volume (${unit()}³)`,
        `Surface area (${unit()}²)`,
        `Equivalent diameter (${unit()})`,
        "Sphericity",
        "Slices",
        `Z extent (${unit()})`,
        `Centroid z, y, x (${unit()})`,
        "Mean intensity",
      ],
      objectRows(data),
      [0, 1, 2, 3, 4, 6, 8],
      "objects",
      "object",
      { rowSlices: data.objects.map((item) => item.centroidSlice), className: "nowrap-table" },
    ));
  } else {
    table.append(node("div", "empty-state", "No objects were found with these settings."));
  }
  panel.append(table);
  return panel;
}

const CATEGORY_RENDERERS = {
  overview: renderOverview,
  zprofile: renderZProfile,
  calibration: renderCalibration,
  segmentation: renderSegmentation,
  objects: renderObjects,
};

function renderTabs() {
  const tabs = byId("categoryTabs");
  tabs.replaceChildren();
  visibleCategories.forEach((category) => {
    const button = node(
      "button",
      `category-tab${category === activeCategory ? " active" : ""}`,
      CATEGORY_LABELS[category],
    );
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(category === activeCategory));
    button.addEventListener("click", () => {
      activeCategory = category;
      renderTabs();
      renderActiveCategory();
    });
    tabs.append(button);
  });
}

function renderActiveCategory() {
  sliceListeners = [];
  const renderer = CATEGORY_RENDERERS[activeCategory];
  byId("resultsContent").replaceChildren(renderer(currentAnalysis));
}

// ------------------------------------------------------------------ search

function collectSearchRows() {
  const rows = [];
  const pushRows = (category, metricRows, targetPrefix) => {
    metricRows.forEach((row, index) => {
      rows.push({
        category,
        label: String(row[0]),
        value: row.slice(1).map(String).join(" · "),
        target: `search-target-${category}-${targetPrefix}-${index}`,
        searchable: row.map(String),
      });
    });
  };
  const pushGraph = (category, label, values, target) => {
    rows.push({
      category,
      label,
      value: `Interactive graph · ${values.length} slices`,
      target,
      searchable: [label, `${values.length} slices`, ...values.map((value) => formatNumber(value))],
    });
  };
  const data = currentAnalysis;
  if (visibleCategories.includes("overview")) {
    data.cards.forEach(([label, value], index) => {
      rows.push({
        category: "overview",
        label,
        value,
        target: `search-target-overview-card-${index}`,
        searchable: [label, value],
      });
    });
  }
  if (visibleCategories.includes("zprofile")) {
    pushGraph(
      "zprofile",
      `${Z_METRICS[activeZMetric]} by slice`,
      data.zProfile.slices.map((item) => item[activeZMetric]),
      "search-target-zprofile-profile",
    );
    pushGraph("zprofile", "Objects per slice", data.objectsPerSlice, "search-target-zprofile-objects");
    pushRows("zprofile", zSummaryRows(data).rows, "summary");
    pushRows(
      "zprofile",
      zSliceRows(data).map((row) => [`Slice ${row[0]}`, ...row.slice(1)]),
      "slice",
    );
  }
  if (visibleCategories.includes("calibration")) {
    pushRows("calibration", calibrationRows(data), "property");
  }
  if (visibleCategories.includes("segmentation")) {
    pushRows("segmentation", segmentationParameterRows(data), "parameter");
    pushRows("segmentation", segmentationResultRows(data), "result");
  }
  if (visibleCategories.includes("objects")) {
    pushRows("objects", objectSummaryRows(data), "summary");
    pushRows("objects", objectRows(data).map((row) => [`Object ${row[0]}`, ...row.slice(1)]), "object");
  }
  return rows;
}

function updateSearch(query) {
  const term = query.trim().toLocaleLowerCase();
  const searchResults = byId("searchResults");
  const searching = term.length > 0;
  searchResults.hidden = !searching;
  byId("resultsContent").hidden = searching;
  byId("categoryTabs").hidden = searching;
  if (!searching) return;
  const matches = collectSearchRows().filter((row) =>
    row.searchable.join(" ").toLocaleLowerCase().includes(term),
  );
  byId("searchResultsTitle").textContent = `${matches.length} matching value${matches.length === 1 ? "" : "s"}`;
  const body = byId("searchResultsBody");
  body.replaceChildren();
  matches.forEach((row) => {
    const tr = node("tr");
    tr.dataset.target = row.target;
    tr.dataset.category = row.category;
    tr.setAttribute("role", "button");
    tr.setAttribute("aria-label", `Open ${CATEGORY_LABELS[row.category]}: ${row.label}`);
    tr.tabIndex = 0;
    const displayedValues = [CATEGORY_LABELS[row.category], row.label, row.value];
    displayedValues.forEach((value) => {
      const cell = node("td");
      appendSearchHighlight(cell, value, term);
      tr.append(cell);
    });
    const matchingSource = row.searchable.find((value) =>
      value.toLocaleLowerCase().includes(term),
    );
    if (
      matchingSource
      && !displayedValues.some((value) => value.toLocaleLowerCase().includes(term))
    ) {
      const context = node("span", "search-match-context");
      context.append(document.createTextNode("Match: "));
      appendSearchHighlight(context, matchingSource, term);
      tr.cells[2].append(node("br"), context);
    }
    body.append(tr);
  });
  if (!matches.length) {
    const tr = node("tr");
    const td = node("td", "empty-state", "No matching values");
    td.colSpan = 3;
    tr.append(td);
    body.append(tr);
  }
}

function appendSearchHighlight(container, text, term) {
  const normalizedText = text.toLocaleLowerCase();
  let cursor = 0;
  let matchIndex = normalizedText.indexOf(term, cursor);
  while (matchIndex !== -1) {
    if (matchIndex > cursor) {
      container.append(document.createTextNode(text.slice(cursor, matchIndex)));
    }
    container.append(node("mark", "search-match", text.slice(matchIndex, matchIndex + term.length)));
    cursor = matchIndex + term.length;
    matchIndex = normalizedText.indexOf(term, cursor);
  }
  if (cursor < text.length || cursor === 0) {
    container.append(document.createTextNode(text.slice(cursor)));
  }
}

function openSearchTarget(resultRow) {
  const category = resultRow.dataset.category;
  const targetId = resultRow.dataset.target;
  if (!category || !visibleCategories.includes(category)) return;
  activeCategory = category;
  renderTabs();
  renderActiveCategory();
  byId("globalSearch").value = "";
  updateSearch("");
  requestAnimationFrame(() => {
    const target = document.getElementById(targetId);
    if (!target) {
      showToast("The matching result is no longer available.", true);
      return;
    }
    target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    target.focus({ preventScroll: true });
    target.classList.remove("search-target-highlight");
    void target.offsetWidth;
    target.classList.add("search-target-highlight");
  });
}

// ------------------------------------------------------------- main screen

function displayAnalysis(data) {
  currentAnalysis = data;
  visibleCategories = getCategories();
  activeCategory = visibleCategories[0];
  byId("globalSearch").value = "";
  byId("searchResults").hidden = true;
  byId("resultsContent").hidden = false;
  byId("categoryTabs").hidden = false;
  updateAnalysisTitle(data);
  byId("resultSubtitle").textContent =
    `${data.summary.count} object${data.summary.count === 1 ? "" : "s"} · ${data.calibration.shape.join(" × ")} voxels · ${data.settings.method} threshold`;
  renderTabs();
  renderActiveCategory();
  setView("results");
  setStatus(`Analysed ${data.channel}: ${data.summary.count} objects.`, "success");
}

function updateAnalysisTitle(data) {
  const reference = data.sample === "cells3d" ? "cells3d" : "";
  byId("resultName").replaceChildren(
    appSettings.enableReferenceLinks && reference
      ? (() => {
          const link = node("a", "reference-link", data.name);
          link.href = "#";
          link.dataset.reference = reference;
          return link;
        })()
      : node("span", "", data.name),
  );
}

function clearAnalysis() {
  currentAnalysis = null;
  visibleCategories = [];
  sliceListeners = [];
  byId("resultsContent").replaceChildren();
  setView("input");
}

function setStackControlsEnabled(enabled) {
  [
    "voxelZ", "voxelY", "voxelX", "unitSelect", "applyCalibrationButton", "scaleBarToggle",
    "view2d", "view3d", "resetViewButton", "screenshotButton", "channelSelect",
    "analyzeButton", "clearStackButton",
  ].forEach((id) => {
    byId(id).disabled = !enabled;
  });
}

function updateUnitLabels() {
  document.querySelectorAll(".unit-label").forEach((label) => {
    label.textContent = `(${unit()}${label.dataset.power === "3" ? "³" : ""})`;
  });
}

function updateSliceIndicator() {
  const indicator = byId("sliceIndicator");
  if (!currentStack) {
    indicator.hidden = true;
    return;
  }
  const depth = currentStack.shape[0];
  indicator.hidden = false;
  indicator.textContent =
    `Slice ${currentSlice} / ${depth - 1} · z ${formatNumber(currentSlice * currentStack.calibration.z)} ${currentStack.calibration.unit}`;
}

function applyDisplay(display) {
  byId("scaleBarToggle").checked = display.scaleBar;
  const is3d = display.ndisplay === 3;
  byId("view2d").classList.toggle("active", !is3d);
  byId("view3d").classList.toggle("active", is3d);
  byId("view2d").setAttribute("aria-pressed", String(!is3d));
  byId("view3d").setAttribute("aria-pressed", String(is3d));
}

function applyStack(stack) {
  currentStack = stack;
  const cal = stack.calibration;
  byId("voxelZ").value = cal.z;
  byId("voxelY").value = cal.y;
  byId("voxelX").value = cal.x;
  byId("unitSelect").value = cal.unit;
  const channelSelect = byId("channelSelect");
  const previous = channelSelect.value;
  channelSelect.replaceChildren(...stack.channels.map((channel) => {
    const option = node("option", "", channel);
    option.value = channel;
    return option;
  }));
  channelSelect.value = stack.channels.includes(previous) ? previous : stack.defaultChannel;
  setStackControlsEnabled(true);
  applyDisplay(stack.display);
  currentSlice = stack.currentSlice;
  updateUnitLabels();
  updateSliceIndicator();
  byId("sampleSelect").value = stack.key;
  byId("sampleStatus").textContent =
    `${stack.name} · ${stack.shape.join(" × ")} voxels · channels: ${stack.channels.join(", ")}`;
}

async function loadSample() {
  const button = byId("loadSampleButton");
  const key = byId("sampleSelect").value;
  setBusy(button, true, "Loading…");
  byId("sampleStatus").textContent = key === "cells3d"
    ? "Loading cells3d (downloads about 30 MB the first time)…"
    : "Generating the synthetic stack…";
  try {
    const result = await invoke("loadSample", key);
    if (!result.ok) {
      byId("sampleStatus").textContent = result.error;
      setStatus(result.error, "error");
      return;
    }
    clearAnalysis();
    applyStack(result.stack);
    setStatus("Sample loaded. Check the calibration, then select Analyse stack.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

async function clearStack() {
  try {
    const result = await invoke("clearStack");
    if (!result.ok) {
      setStatus(result.error, "error");
      return;
    }
    currentStack = null;
    clearAnalysis();
    setStackControlsEnabled(false);
    ["voxelZ", "voxelY", "voxelX"].forEach((id) => { byId(id).value = ""; });
    byId("channelSelect").replaceChildren();
    applyDisplay({ scaleBar: byId("scaleBarToggle").checked, ndisplay: 2 });
    updateSliceIndicator();
    byId("sampleStatus").textContent = "No stack loaded.";
    setStatus("Stack cleared.");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function applyCalibration(event) {
  event.preventDefault();
  const button = byId("applyCalibrationButton");
  setBusy(button, true, "Applying…");
  try {
    const result = await invoke("setCalibration", JSON.stringify({
      z: byId("voxelZ").value,
      y: byId("voxelY").value,
      x: byId("voxelX").value,
      unit: byId("unitSelect").value,
    }));
    if (!result.ok) {
      setStatus(result.error, "error");
      return;
    }
    const cleared = result.analysisCleared;
    clearAnalysis();
    applyStack(result.stack);
    setStatus(
      cleared
        ? "Calibration applied. The previous analysis was cleared because its physical values are now stale."
        : "Calibration applied.",
      "success",
    );
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

// Optimistic update: show the new state at once, revert if Python refuses it.
async function changeDisplay(change, previous) {
  applyDisplay({ ...previous, ...change });
  try {
    const result = await invoke("setDisplay", JSON.stringify(change));
    if (!result.ok) {
      applyDisplay(result.display || previous);
      showToast(result.error, true);
      return;
    }
    applyDisplay(result.display);
    if (currentStack) currentStack.display = result.display;
  } catch (error) {
    applyDisplay(previous);
    showToast(error.message, true);
  }
}

function currentDisplay() {
  return {
    scaleBar: byId("scaleBarToggle").checked,
    ndisplay: byId("view3d").classList.contains("active") ? 3 : 2,
  };
}

async function analyzeStack(event) {
  event.preventDefault();
  const button = byId("analyzeButton");
  setBusy(button, true, "Analysing…");
  setStatus("Segmenting and measuring objects…");
  try {
    const result = await invoke("analyzeStack", JSON.stringify({
      channel: byId("channelSelect").value,
      method: byId("methodSelect").value,
      manualThreshold: byId("manualThreshold").value,
      sigma: byId("sigmaInput").value,
      minVolume: byId("minVolumeInput").value,
      splitTouching: byId("splitToggle").checked,
      minDistance: byId("minDistanceInput").value,
    }));
    if (!result.ok) {
      setStatus(result.error, "error");
      return;
    }
    currentStack = result.stack;
    displayAnalysis(result.analysis);
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(button, false);
    button.disabled = currentStack === null;
  }
}

function applyAnalysisSettings(settings, channel) {
  byId("methodSelect").value = settings.method;
  byId("manualThreshold").value = settings.manualThreshold ?? "";
  byId("manualThreshold").disabled = settings.method !== "manual";
  byId("sigmaInput").value = settings.sigma;
  byId("minVolumeInput").value = settings.minVolume;
  byId("minDistanceInput").value = settings.minDistance;
  byId("splitToggle").checked = settings.splitTouching;
  byId("channelSelect").value = channel;
}

async function simpleAction(method, ...args) {
  try {
    const result = await invoke(method, ...args);
    if (result.ok && result.message) showToast(result.message);
    else if (!result.ok && !result.cancelled) showToast(result.error, true);
    return result;
  } catch (error) {
    showToast(error.message, true);
    return null;
  }
}

async function openAnalysis() {
  try {
    setStatus("Importing analysis and re-running it…");
    const result = await invoke("openAnalysis");
    if (result.cancelled) {
      setStatus("");
      return;
    }
    if (!result.ok) {
      setStatus(result.error, "error");
      showToast(result.error, true);
      return;
    }
    applyStack(result.stack);
    applyAnalysisSettings(result.analysis.settings, result.analysis.channel);
    displayAnalysis(result.analysis);
    if (result.importNote) showToast(result.importNote);
  } catch (error) {
    showToast(error.message, true);
  }
}

// ---------------------------------------------------------------- settings

function buildSettingsCategories() {
  const container = byId("categorySettings");
  container.replaceChildren();
  Object.entries(CATEGORY_LABELS).forEach(([key, label]) => {
    const choice = node("label", "category-choice");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = key;
    checkbox.checked = key === "overview" || appSettings.categories[key] !== false;
    checkbox.disabled = key === "overview";
    choice.append(checkbox, node("span", "", label));
    container.append(choice);
  });
}

function openSettings() {
  buildSettingsCategories();
  byId("fontSizeSetting").value = String(appSettings.fontSize);
  byId("startMaximized").checked = appSettings.startMaximized;
  byId("fullScreenSetting").checked = appSettings.fullScreen;
  byId("enableReferenceLinks").checked = appSettings.enableReferenceLinks;
  byId("settingsDialog").showModal();
}

async function saveSettings() {
  const categories = {};
  byId("categorySettings")
    .querySelectorAll('input[type="checkbox"]')
    .forEach((checkbox) => {
      categories[checkbox.name] = checkbox.checked;
    });
  const settings = {
    categories,
    fontSize: Number(byId("fontSizeSetting").value),
    startMaximized: byId("startMaximized").checked,
    fullScreen: byId("fullScreenSetting").checked,
    enableReferenceLinks: byId("enableReferenceLinks").checked,
  };
  try {
    const result = await invoke("saveSettings", JSON.stringify(settings));
    if (!result.ok) {
      showToast(result.error, true);
      return;
    }
    appSettings = result.settings;
    applyTheme(appSettings.themeMode, appSettings.effectiveTheme);
    if (currentAnalysis) {
      updateAnalysisTitle(currentAnalysis);
      visibleCategories = getCategories();
      if (!visibleCategories.includes(activeCategory)) activeCategory = visibleCategories[0];
      renderTabs();
      renderActiveCategory();
      if (byId("globalSearch").value) updateSearch(byId("globalSearch").value);
    }
    byId("settingsDialog").close();
    showToast("Settings saved.");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function changeTheme() {
  const mode = byId("themeSelect").value;
  try {
    const result = await invoke("setTheme", mode);
    if (!result.ok) {
      showToast(result.error, true);
      return;
    }
    appSettings.themeMode = mode;
    applyTheme(mode, result.effectiveTheme);
  } catch (error) {
    showToast(error.message, true);
  }
}

function bindEvents() {
  byId("mainNav").addEventListener("click", () => setView("input"));
  byId("dataNav").addEventListener("click", () => setView("results"));
  byId("sidebarToggle").addEventListener("click", () => {
    const collapsed = byId("sidebar").classList.toggle("collapsed");
    byId("appShell").classList.toggle("sidebar-is-collapsed", collapsed);
    byId("sidebarToggle").textContent = collapsed ? "›" : "‹";
    byId("sidebarToggle").title = collapsed ? "Expand sidebar" : "Collapse sidebar";
    byId("sidebarToggle").setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
  });
  byId("themeSelect").addEventListener("change", changeTheme);
  byId("settingsButton").addEventListener("click", openSettings);
  byId("saveSettings").addEventListener("click", saveSettings);
  byId("loadSampleButton").addEventListener("click", loadSample);
  byId("clearStackButton").addEventListener("click", clearStack);
  byId("calibrationForm").addEventListener("submit", applyCalibration);
  byId("scaleBarToggle").addEventListener("change", (event) => {
    const previous = { ...currentDisplay(), scaleBar: !event.target.checked };
    changeDisplay({ scaleBar: event.target.checked }, previous);
  });
  byId("view2d").addEventListener("click", () => changeDisplay({ ndisplay: 2 }, currentDisplay()));
  byId("view3d").addEventListener("click", () => changeDisplay({ ndisplay: 3 }, currentDisplay()));
  byId("resetViewButton").addEventListener("click", () => simpleAction("resetView"));
  byId("screenshotButton").addEventListener("click", () => simpleAction("saveScreenshot"));
  byId("methodSelect").addEventListener("change", () => {
    byId("manualThreshold").disabled = byId("methodSelect").value !== "manual";
  });
  byId("analysisForm").addEventListener("submit", analyzeStack);
  byId("importAnalysisButton").addEventListener("click", openAnalysis);
  byId("exportButton").addEventListener("click", () => simpleAction("exportAnalysis", currentAnalysis.name));
  byId("exportCsvButton").addEventListener("click", () => simpleAction("exportObjectsCsv", currentAnalysis.name));
  byId("globalSearch").addEventListener("input", (event) => updateSearch(event.target.value));
  byId("clearSearch").addEventListener("click", () => {
    byId("globalSearch").value = "";
    updateSearch("");
    byId("globalSearch").focus();
  });
  byId("searchResultsBody").addEventListener("click", (event) => {
    const resultRow = event.target.closest("tr[data-target]");
    if (resultRow) openSearchTarget(resultRow);
  });
  byId("searchResultsBody").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const resultRow = event.target.closest("tr[data-target]");
    if (!resultRow) return;
    event.preventDefault();
    openSearchTarget(resultRow);
  });
  document.addEventListener("click", async (event) => {
    const referenceLink = event.target.closest("a[data-reference]");
    if (!referenceLink) return;
    event.preventDefault();
    try {
      const result = await invoke("openReference", referenceLink.dataset.reference);
      if (!result.ok) showToast(result.error, true);
    } catch (error) {
      showToast(error.message, true);
    }
  });
  byId("backToInput").addEventListener("click", () => setView("input"));
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (!byId("searchControl").hidden) byId("globalSearch").focus();
    }
  });
}

function populateChoices() {
  byId("sampleSelect").replaceChildren(...appSettings.samples.map((sample) => {
    const option = node("option", "", sample.label);
    option.value = sample.key;
    return option;
  }));
  byId("sampleSelect").value = "synthetic";
  byId("unitSelect").replaceChildren(...appSettings.units.map((value) => {
    const option = node("option", "", value);
    option.value = value;
    return option;
  }));
  applyAnalysisSettings(appSettings.defaults, "");
  updateUnitLabels();
}

function initialize() {
  bindEvents();
  if (typeof qt === "undefined" || !qt.webChannelTransport) {
    setStatus("The embedded desktop bridge is unavailable.", "error");
    return;
  }
  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.bridge;
    bridge.sliceChanged.connect((index) => {
      currentSlice = index;
      updateSliceIndicator();
      sliceListeners.forEach((listener) => listener());
    });
    bridge.getInitialState((raw) => {
      try {
        appSettings = JSON.parse(raw);
        applyTheme(appSettings.themeMode, appSettings.effectiveTheme);
        populateChoices();
        if (bridge.systemThemeChanged) {
          bridge.systemThemeChanged.connect((theme) => {
            if (appSettings.themeMode === "System") {
              appSettings.effectiveTheme = theme;
              applyTheme("System", theme);
            }
          });
        }
        setStatus("Choose a sample and select Load sample.");
      } catch (error) {
        setStatus(`Unable to load application settings: ${error.message}`, "error");
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", initialize);
