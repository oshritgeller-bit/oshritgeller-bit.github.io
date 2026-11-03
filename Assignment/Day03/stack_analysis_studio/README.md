# Day 03 – Stack Analysis Studio

## Overview

Stack Analysis Studio is a desktop application for analysing 3D image stacks (z-stacks). The web interface sits on the left, and an embedded **napari** viewer sits on the right in the same window.

It follows the structure and style of the reference project [Protein Analysis Studio](https://github.com/ron075/course_assignments/tree/main/day03). It has the same Python ↔ JavaScript bridge, sidebar, result tabs, settings dialog, global search (Ctrl K), toasts and Markdown import/export.

The app provides three analyses:

- **Scale bar and calibration**: you can view and change the voxel size (z / y / x) and unit (µm, nm, mm). napari shows a calibrated scale bar, and every physical value (z axis, volumes, areas) uses the calibration. Changing the calibration clears the current analysis, because its physical values would be out of date.
- **Z-axis profiles**: for each slice, the mean, maximum and standard deviation of intensity, a focus measure (variance of the Laplacian), the foreground percentage and the signal-to-noise ratio (using one Otsu threshold for the whole stack). It also reports the brightest slice, the best-focus slice and the half-intensity depth.
- **3D segmentation and object statistics**: Gaussian smoothing (sigma in physical units), an Otsu, Li or manual threshold, 26-connected labelling, a minimum-volume filter and an optional watershed to split touching objects. For each object it measures the volume, surface area, equivalent diameter, sphericity, z extent, centroid and mean intensity.

The charts and napari are linked in both directions:

- Clicking a point on a z-chart, or a row in a table, moves napari to that slice.
- Moving the napari slider moves the slice marker on the charts and highlights the matching table row.

The display controls are the scale bar on/off, 2D/3D view, reset view and save screenshot. Results can be exported as a Markdown report (with embedded data, so the report can be imported again) and as a CSV table of objects.

Data: napari / scikit-image sample data only.

- **cells3d**: 60 × 2 × 256 × 256 voxels, nuclei and membranes, voxel size 0.29 / 0.26 / 0.26 µm. It is downloaded the first time it is used.
- **Synthetic blobs**: a generated stack that works offline. It is 48 × 160 × 160 voxels, voxel size 0.5 / 0.25 / 0.25 µm, and the blobs get dimmer with depth.

## Technologies

- **Python**: analysis (NumPy, SciPy, scikit-image) and application logic.
- **PySide6**: the desktop window, with Qt WebEngine and Qt WebChannel as the bridge to the web UI.
- **napari**: the embedded viewer (`ViewerModel` + `QtViewer`, with docked layer controls and the layer list).
- **HTML, CSS, JavaScript**: the user interface and the charts.

Project layout:

| File | Purpose |
|:--|:--|
| `run_app.py` | Launcher: checks Python ≥ 3.11, installs missing requirements, starts `main.py` |
| `app.py` | `BridgeObject` (slots/signals), `AnalysisBridge` (state + payloads), `StackAnalyzerWindow` (web UI ⟷ napari splitter) |
| `napari_panel.py` | `NapariPanel`: embedded napari viewer, layers, scale bar, slice sync, 2D/3D, screenshot, theme |
| `stack_analysis.py` | Qt-free analysis (calibration, samples, z-profile, segmentation, object statistics) |
| `reporting.py` | Markdown report with embedded JSON (`schema_version: 1`), import validation, objects CSV |
| `web/` | `index.html`, `app.js`, `styles.css` (based on the reference files) |
| `test_*.py` | Analysis unit tests, napari panel test, end-to-end UI test |

All measured values are estimates from classical image analysis. They are not validated measurements.

## How to Run

Requires **Python 3.11 or newer** (napari 0.10 needs it) and a computer with OpenGL.

**Windows:**
```powershell
py run_app.py
```

**macOS / Linux:**
```bash
python3 run_app.py
```

The launcher checks the packages in `requirements.txt` and installs any that are missing.

Notes:

- **cells3d** needs internet the first time (about 30 MB, cached afterwards). If the download fails, the app shows an error and suggests the synthetic sample.
- The Python must have **lzma (xz)** support, because `pooch` (the downloader) needs it. Some pyenv builds don't have it. The app detects this and says so. To fix it: `brew install xz`, then `pyenv install --force 3.12`.
- **Linux** needs `libxcb-cursor0` for the Qt xcb plugin. Headless or remote machines need a virtual display (`xvfb-run`).

Tests:

```bash
python test_stack_analysis.py     # analysis: sphere volume/area, watershed split, empty/single-voxel, JSON-safety
python test_napari_panel.py       # napari panel: layers, units, zChanged, labels, 3D, screenshot
python test_app_e2e.py            # whole app driven through the UI (add --cells3d, --screenshots DIR)
```

## Limitations

- Sample data only. Loading your own TIFF / OME-Zarr files is not included; it would be a natural next step.
- Only classical thresholding and watershed are used (no deep-learning segmentation). Touching or irregular objects can be over-split or under-split. In the synthetic stack, one blob is split in two while a touching pair stays merged.
- Objects that touch the stack border are cut off. There is no deconvolution or PSF correction, so sizes are estimates (z is especially blurred).
- Focus (Laplacian variance) is a relative measure and also increases with noise.
- Surface area uses marching cubes on a slightly blurred mask. Measured on spheres it is within about 1–2% at a radius of 10 voxels and 1–6% at 5 voxels, but it is underestimated by 30–55% below 3 voxels. Sphericity of very small objects is therefore unreliable (and capped at 1).
- The analysis runs on the UI thread, so the window pauses while it works. This takes about 1 s for the synthetic stack and 12–16 s per cells3d channel.
- The app uses the native title bar instead of the reference's frameless window, so napari's docks behave normally.

## AI-Assisted Development

Claude (Opus 5.5) in Visual Studio Code generated the app from a written specification (`../STACK_ANALYSIS_STUDIO_PROMPT.md`). The specification described:

- the reference project to copy (structure, naming, look);
- the three analyses and the napari integration;
- known pitfalls: `get_qapp` for napari icons, `canvas.overlays.scale_bar` instead of the deprecated `viewer.scale_bar`, never setting `camera.zoom` before `reset_view()`, parenting the QWebChannel to its page, and blurring the mask before marching cubes;
- a step-by-step plan with tests at each step.

## Process

1. The reference repository was read first. Its bridge (`@Slot` methods that return JSON, called through a JS `invoke()` helper), settings, theme, search and chart code were reused.
2. `stack_analysis.py` was written without Qt and tested first: sphere volume within 1%, area within 5%, the watershed splits two touching spheres, empty and single-voxel inputs don't crash, and every output can be saved as JSON.
3. `napari_panel.py` was tested on its own (layers, units, slice signal, labels, 3D camera, screenshot).
4. The app, reporting, launcher and web UI were written next. An end-to-end script then drove the real window: loading, validation errors, analysis, every tab, chart/table ↔ napari linking, 2D/3D, scale bar, export → import round trip, CSV, screenshot, theme, settings, narrow width and clear.
5. Screenshots of each view were checked by eye. That check found and fixed:
   - table headers in capitals, which turned "µm³" into "MM³";
   - the collapsed-sidebar button covering the page title;
   - wrapped unit hints breaking the form grid;
   - sidebar labels being clipped.

   Testing also showed that on macOS the web view must use OpenGL (`QQuickWindow.setGraphicsApi`) when it sits next to napari's OpenGL canvas; without that it does not render.
