# Instruction for Claude (Opus 5.5) in VS Code: build "Stack Analysis Studio"

Paste this whole file into the Claude plugin chat, or save it in the repo as `PROMPT.md` and say "Follow PROMPT.md". Work step by step and ask for my approval after each numbered step in section 6.

---

## 1. Request summary

I am a master's student and this is homework. I need a desktop app that analyses 3D image stacks (z-stacks) opened in napari. It must keep the structure and style of my reference project:
https://github.com/ron075/course_assignments/tree/main/day03 (Protein Analysis Studio).

Read that folder first (`app.py`, `run_app.py`, `reporting.py`, `web/index.html`, `web/app.js`, `web/styles.css`, `README.md`) and copy its patterns. Do not modify or push to that repo.

Required analyses:
1. **Scale bar and calibration** (voxel size z/y/x, unit, scale bar in napari).
2. **Z-axis profiles** (per-slice intensity and quality metrics).
3. **3D segmentation and object statistics.**

Data: napari sample data only (no user files). napari must be **embedded side by side** with the web UI, not opened as a separate window.

## 2. Proposed solution

A PySide6 desktop app. The left pane is the web UI (QWebEngineView + QWebChannel, HTML/CSS/JS). The right pane is an embedded napari viewer. A Python backend does the analysis. A bridge object exposes `@Slot` methods that return JSON strings, and JS calls them through an `invoke(method, ...args)` helper, exactly as in the reference `app.py`.

### Project layout

```
stack_analysis_studio/
  run_app.py          # launcher: checks Python >= 3.11, installs requirements, starts main.py
  main.py             # calls app.main()
  app.py              # BridgeObject (slots/signals), AnalysisBridge (state + payload), StackAnalyzerWindow
  napari_panel.py     # NapariPanel(QMainWindow): ViewerModel + QtViewer + docked controls/layer list
  stack_analysis.py   # Qt-free analysis (numpy/scipy/scikit-image), unit-testable
  reporting.py        # Markdown export with embedded JSON (round-trip import), objects CSV
  requirements.txt    # PySide6>=6.5, napari>=0.9, numpy>=1.25, scipy>=1.14, scikit-image>=0.21, pooch>=1.7
  app_icon.svg
  README.md           # same sections as the reference README
  web/index.html, web/app.js, web/styles.css
```

### Analysis module (`stack_analysis.py`, no Qt imports)

- `Calibration(z, y, x, unit)`: validates positive, finite values. Units: µm, nm, mm. Properties: `scale`, `voxel_volume`, `anisotropy`.
- `StackData(name, channels: dict[str, ndarray], calibration, source)`.
- Samples: scikit-image `cells3d` (shape 60×2×256×256, voxel 0.29/0.26/0.26 µm z/y/x, channel 0 membranes, channel 1 nuclei; needs a first-use download) and a **synthetic offline stack** (e.g. 48×160×160, voxel 0.5/0.25/0.25 µm, blobs with depth attenuation, seeded RNG).
- `nice_length(...)`: scale-bar length rounded to 1/2/5 × 10^n.
- `z_profile(volume, cal)`: per slice mean, max, std, focus (variance of the 2D Laplacian), foreground % and SNR with one stack-wide Otsu threshold, plus half-intensity depth.
- `segment_3d(volume, cal, method, manual_threshold, sigma, min_volume, split_touching, min_distance)` returns `(labels, info)`:
  Gaussian smoothing with sigma in physical units converted per axis to voxels, then Otsu, Li or manual threshold, then 26-connected labelling, then minimum-volume filter, then optional watershed on the distance transform (use the voxel size as `sampling`).
- `object_stats`: voxel count, volume, surface area (marching cubes on a Gaussian-blurred, padded mask, blur ≈ 0.75 × smallest voxel edge, fallback to the raw mask), equivalent diameter, sphericity, z extent, centroid in physical units, mean raw intensity. Also `objects_per_slice` and `summarize_objects`.
- All outputs must be JSON-safe (no NaN or numpy types).

### napari panel (`napari_panel.py`)

- Use `ViewerModel` + `napari.qt.QtViewer`, not `napari.Viewer`. Put `qt_viewer.controls` and `qt_viewer.layers` in QDockWidgets inside a QMainWindow with `Qt.WindowType.Widget`.
- Set `os.environ.setdefault("QT_API", "pyside6")` before importing napari.
- Image layers get `scale=cal.scale` and `units=("micrometer",)*3` (unit names are full words: micrometer, nanometer, millimeter).
- Scale bar through `viewer.canvas.overlays.scale_bar` (visible, length, box, colored, ticks, position). The old `viewer.scale_bar` is deprecated in napari 0.10.
- Labels layer named "Objects", opacity 0.55. Slice sync with `viewer.dims.set_current_step(0, i)` and `viewer.dims.events.current_step`, emitted as a Qt signal `zChanged(int)`.
- 2D/3D via `viewer.dims.ndisplay`. In 3D call `reset_view()` first, then set `camera.angles = (0, -40, 140)`. Do not set `camera.zoom` manually.
- `save_screenshot` via `qt_viewer.screenshot(path, flash=False)`; theme via `get_stylesheet`.
- In `main()`: set `QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)` **before** creating the app, and create it with `napari.qt.get_qapp(...)` so napari's icons are registered.

### Web UI (same look as the reference)

- Sidebar (collapsed by default), main/input view, results view with category tabs, settings dialog, global search (Ctrl K), toast. Reuse the reference helper layer in `app.js` (`invoke`, `createTable`, `referenceHeading`, `settings`, `theme`) and its line/bar chart renderers. Copy `styles.css` from the reference, then add the new components and remove unused rules.
- Input view: load a sample, "Calibration and display" card (voxel z/y/x, unit, apply, scale bar on/off, 2D/3D, reset view, save screenshot), "Analysis settings" card (channel, threshold method, manual threshold, sigma, minimum volume, split touching, minimum distance), analyse button.
- Result tabs: Overview, Z-axis profile, Calibration, Segmentation, Objects. Slice marker on Z charts, clickable chart points and table rows that jump napari to that slice, and a `sliceChanged` signal from Python to JS.
- Slots: `getInitialState, loadSample, clearStack, setCalibration, setDisplay, goToSlice, resetView, analyzeStack, saveScreenshot, openAnalysis, exportAnalysis, exportObjectsCsv, saveSettings, setTheme, openReference, toggleMaximized, toggleFullScreen`.
- Display toggles use an optimistic update with a revert on error.
- Changing the calibration clears the analysis and labels (physical values would be stale).
- Export Markdown with a JSON block between marker comments (`schema_version: 1`) and validate it on import.
- Add a "Methods and limitations" section to the report.

## 3. Advantages

- Matches the reference architecture, so it is easy to explain and grade as "same structure and style".
- napari is embedded beside the web UI, so the scale bar, slices and 3D view stay linked to the charts in both directions.
- Calibration is applied everywhere (scale bar, volumes, areas, z axis), and anisotropy is handled in smoothing, watershed and surface area.
- The analysis module is free of Qt, so it can be unit-tested and reused in a notebook.
- Works offline with the synthetic stack, and the Markdown/CSV export is reproducible and re-importable.

## 4. Limitations (state these honestly in the README)

- Sample data only. Loading the user's own TIFF/OME-Zarr files is not included (suggest as a next step).
- `cells3d` needs internet on first use. Show a clear error and point to the synthetic sample if the download fails.
- Classical thresholding and watershed only, with no deep-learning segmentation. Touching or irregular objects can be over- or under-split.
- Objects touching the border are truncated. No deconvolution or PSF correction, so sizes are estimates.
- Focus (Laplacian variance) is relative and also rises with noise. Surface area is accurate to about 4% for spheres of radius ≥ 1.5 voxel units.
- napari 0.10 needs Python ≥ 3.11. Embedding napari with PySide6 needs OpenGL, and headless/remote machines need a virtual display.
- Native title bar is used instead of the reference's frameless window, so napari docks behave normally.

## 5. Known pitfalls (avoid these)

1. napari 0.10 icons are missing unless you use `get_qapp`.
2. `viewer.scale_bar` and its `unit` field are deprecated or removed. Use `canvas.overlays.scale_bar` and layer `units`.
3. Setting `camera.zoom` before `reset_view()` makes the 3D volume tiny.
4. Do not replace a QWebEnginePage whose QWebChannel is parented to the old page (crash). Parent the channel to the page it serves.
5. Linux needs `libxcb-cursor0` for the Qt xcb plugin.
6. Raw marching-cubes surface area on a binary mask over-estimates by ~10%. Blur the mask first.

## 6. Steps (stop for my approval after each)

1. Read the reference repo and propose the file layout and the signal/slot list. Wait for approval.
2. Write `stack_analysis.py` and a test script (sphere volume within 1%, area within ~5%, watershed splits two touching spheres, empty segmentation and single-voxel cases do not crash, output is JSON-serialisable).
3. Write `napari_panel.py` and test it headless (`xvfb-run`, software OpenGL): layers, units, `zChanged`, labels, 3D, screenshot.
4. Write `app.py`, `reporting.py`, `run_app.py`, `main.py`, `requirements.txt`.
5. Write `web/index.html`, `web/app.js`, `web/styles.css` (start from the reference files).
6. End-to-end test: load sample, analyse, open every tab, chart click and table click move napari, slider moves chart marker, 2D/3D, scale-bar toggle, calibration change clears results, validation errors, Markdown export then import round trip, CSV export, screenshot, theme, settings, clear.
7. Visually check screenshots of every view (also light theme and narrow width). Fix clipped text.
8. Write `README.md` with the reference's sections (Overview, Technologies, How to Run, AI-Assisted Development, Process) plus the limitations above.

## 7. Rules for you

- Follow the reference style and naming. Keep the code simple and commented where the logic is not obvious.
- Do not claim something works unless you ran it. Say clearly what you could not test (for example the `cells3d` download without internet).
- Do not add features I did not ask for.
- At the end give a short summary: what was built, what was tested, what was not, and how to run it.
