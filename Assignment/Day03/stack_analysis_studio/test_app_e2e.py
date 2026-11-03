"""End-to-end test: drives the real window (web UI + embedded napari) via JavaScript.

    python test_app_e2e.py                    # synthetic sample only (offline)
    python test_app_e2e.py --cells3d          # also load + analyse cells3d (needs internet once)
    python test_app_e2e.py --screenshots DIR  # save a PNG of every view for a visual check

Needs OpenGL (on Linux without a display: xvfb-run -a python test_app_e2e.py).
File dialogs are replaced by temporary paths; settings go to a temporary folder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog

import app as studio

studio.configure_qt()
from napari.qt import get_qapp  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="stack_studio_e2e_"))
dialog_paths = {"save": "", "open": ""}
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (dialog_paths["save"], ""))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (dialog_paths["open"], ""))


class Driver:
    def __init__(self, window: studio.StackAnalyzerWindow) -> None:
        self.window = window
        self.page = window.web_view.page()
        self.app = QApplication.instance()

    def js(self, script: str, timeout: float = 20):
        box = {}
        self.page.runJavaScript(f"JSON.stringify((() => {{ {script} }})())", 0, lambda r: box.setdefault("r", r))
        end = time.time() + timeout
        while "r" not in box:
            if time.time() > end:
                raise TimeoutError(script)
            self.app.processEvents()
            time.sleep(0.005)
        return None if box["r"] in (None, "", "undefined") else json.loads(box["r"])

    def wait(self, condition: str, timeout: float = 60, what: str = ""):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(f"return Boolean({condition});"):
                return
            self.pause(0.05)
        raise AssertionError(f"Timed out waiting for {what or condition}")

    def pause(self, seconds: float):
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def click(self, element_id: str):
        self.js(f"document.getElementById('{element_id}').click();")

    def set_value(self, element_id: str, value):
        self.js(f"document.getElementById('{element_id}').value = {json.dumps(value)};")

    def submit(self, form_id: str):
        self.js(f"document.getElementById('{form_id}').requestSubmit();")

    def status(self) -> str:
        return self.js("return document.getElementById('appStatus').textContent;")

    def click_chart_at_slice(self, card_id: str, index: int, count: int):
        # Same geometry as renderLineChart: plot spans x = 66..780 in an 800×310 viewBox.
        self.js(f"""
          const svg = document.querySelector('#{card_id} svg');
          const b = svg.getBoundingClientRect();
          const scale = Math.min(b.width / 800, b.height / 310);
          const inset = (b.width - 800 * scale) / 2;
          const x = b.left + inset + (66 + {index} / {count - 1} * 714) * scale;
          const y = b.top + 140 * scale;
          const opts = {{bubbles: true, clientX: x, clientY: y, button: 0, pointerId: 1}};
          svg.dispatchEvent(new PointerEvent('pointerdown', opts));
          svg.dispatchEvent(new PointerEvent('pointerup', opts));
        """)

    def shot(self, folder: Path | None, name: str):
        if folder is None:
            return
        self.pause(0.4)
        self.window.grab().save(str(folder / f"{name}.png"))


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def run(args) -> int:
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(TMP / "settings"))
    app = get_qapp()
    window = studio.StackAnalyzerWindow()
    window.resize(1500, 920)
    window.show()
    d = Driver(window)
    bridge = window.bridge
    panel = window.napari_panel
    shots = Path(args.screenshots).resolve() if args.screenshots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    d.wait("typeof appSettings !== 'undefined' && appSettings !== null", what="web UI ready")
    d.js("window.__errors = []; addEventListener('error', e => __errors.push(e.message));"
         "addEventListener('unhandledrejection', e => __errors.push(String(e.reason)));")
    d.shot(shots, "01_start")

    print("Load sample")
    d.set_value("sampleSelect", "synthetic")
    d.click("loadSampleButton")
    d.wait("currentStack !== null", what="synthetic stack")
    check(len(panel.viewer.layers) == 1 and panel.viewer.layers[0].name == "blobs", "napari shows the blobs layer")
    check(panel.viewer.canvas.overlays.scale_bar.visible, "scale bar visible after load")
    check(d.js("return document.getElementById('voxelZ').value;") == "0.5", "calibration inputs filled")
    check("Slice 24" in d.js("return document.getElementById('sliceIndicator').textContent;"), "slice indicator shows middle slice")
    d.shot(shots, "02_loaded")

    print("Validation errors")
    d.set_value("sigmaInput", "-1")
    d.submit("analysisForm")
    d.wait("document.getElementById('appStatus').classList.contains('error')", what="sigma error")
    check("sigma" in d.status().lower(), f"negative sigma rejected: {d.status()}")
    d.set_value("sigmaInput", "0.5")
    d.set_value("methodSelect", "manual")
    d.js("document.getElementById('methodSelect').dispatchEvent(new Event('change'));")
    d.set_value("manualThreshold", "")
    d.submit("analysisForm")
    d.wait("document.getElementById('appStatus').textContent.includes('manual')", what="manual error")
    check(True, f"empty manual threshold rejected: {d.status()}")
    d.set_value("methodSelect", "otsu")
    d.js("document.getElementById('methodSelect').dispatchEvent(new Event('change'));")
    d.set_value("voxelZ", "0")
    d.submit("calibrationForm")
    d.wait("document.getElementById('appStatus').textContent.includes('Voxel size z')", what="voxel error")
    check(bridge.calibration.z == 0.5, f"zero voxel size rejected: {d.status()}")
    d.set_value("voxelZ", "0.5")

    print("Analyse")
    d.submit("analysisForm")
    d.wait("currentAnalysis !== null", what="analysis")
    count = bridge.last_analysis["summary"]["count"]
    check(count > 5, f"{count} objects found")
    check("Objects" in panel.viewer.layers, "labels layer added to napari")
    tabs = d.js("return [...document.querySelectorAll('.category-tab')].map(t => t.textContent);")
    check(tabs == list(studio.StackAnalyzerWindow.CATEGORY_LABELS.values()), f"tabs: {tabs}")
    d.shot(shots, "03_overview")

    for index, name in enumerate(["zprofile", "calibration", "segmentation", "objects"], start=2):
        d.js(f"document.querySelectorAll('.category-tab')[{index - 1}].click();")
        d.wait("document.getElementById('resultsContent').children.length > 0")
        check(d.js("return activeCategory;") == name, f"{name} tab renders")
        d.shot(shots, f"0{index + 2}_{name}")

    print("Linking napari ↔ charts/tables")
    d.js("document.querySelectorAll('.category-tab')[1].click();")
    depth = bridge.stack.shape[0]
    d.click_chart_at_slice("search-target-zprofile-profile", 7, depth)
    d.pause(0.3)
    check(panel.current_slice == 7, f"chart click moved napari to slice {panel.current_slice}")
    d.js("document.querySelector('tr[data-slice=\"12\"]').click();")
    d.pause(0.3)
    check(panel.current_slice == 12, "table row click moved napari to slice 12")
    panel.go_to_slice(20)
    d.wait("document.querySelector('.chart-marker-label')?.textContent === 'slice 20'", what="marker")
    check(True, "napari slider change moved the chart marker")
    check(d.js("return document.querySelector('tr.current-slice')?.dataset.slice;") == "20", "current slice row highlighted")
    d.js("document.querySelectorAll('.category-tab')[4].click();")
    d.js("document.querySelector('#resultsContent tr[data-slice]').click();")
    d.pause(0.3)
    check(panel.current_slice == bridge.last_analysis["objects"][0]["centroidSlice"], "object row click jumps to centroid slice")

    print("Search")
    d.set_value("globalSearch", "Object 1")
    d.js("document.getElementById('globalSearch').dispatchEvent(new Event('input'));")
    hits = d.js("return document.querySelectorAll('#searchResultsBody tr[data-target]').length;")
    check(hits >= 1, f"search found {hits} rows")
    d.shot(shots, "08_search")
    d.js("document.querySelector('#searchResultsBody tr[data-target]').click();")
    check(d.js("return document.getElementById('searchResults').hidden;"), "search result opens its tab")

    print("Display")
    d.click("backToInput")
    d.click("view3d")
    d.wait("document.getElementById('view3d').classList.contains('active')")
    d.pause(0.3)
    check(panel.viewer.dims.ndisplay == 3, "3D view on")
    d.shot(shots, "09_3d")
    d.click("view2d")
    d.pause(0.3)
    check(panel.viewer.dims.ndisplay == 2, "back to 2D")
    d.click("scaleBarToggle")
    d.pause(0.3)
    check(not panel.viewer.canvas.overlays.scale_bar.visible, "scale bar toggled off")
    d.click("scaleBarToggle")
    d.pause(0.3)
    check(panel.viewer.canvas.overlays.scale_bar.visible, "scale bar toggled on")
    d.js("changeDisplay({ndisplay: 4}, currentDisplay());")
    d.pause(0.4)
    check(d.js("return document.getElementById('view2d').classList.contains('active');"), "invalid display reverted (optimistic update)")

    print("Files")
    report = TMP / "report.md"
    dialog_paths["save"] = str(report)
    d.click("exportButton")
    d.pause(0.5)
    check(report.is_file() and "STACK_ANALYSIS_STUDIO_DATA_START" in report.read_text(encoding="utf-8"), "Markdown exported")
    check("## Methods and limitations" in report.read_text(encoding="utf-8"), "report has methods and limitations")
    csv_path = TMP / "objects.csv"
    dialog_paths["save"] = str(csv_path)
    d.click("exportCsvButton")
    d.pause(0.5)
    check(len(csv_path.read_text(encoding="utf-8-sig").splitlines()) == count + 1, "CSV has one row per object")
    shot = TMP / "shot.png"
    dialog_paths["save"] = str(shot)
    d.click("screenshotButton")
    d.pause(0.8)
    check(shot.is_file() and shot.stat().st_size > 1000, "napari screenshot saved")

    print("Calibration change clears results")
    d.set_value("voxelZ", "1")
    d.submit("calibrationForm")
    d.wait("currentAnalysis === null && document.getElementById('dataNav').disabled", what="cleared")
    check("Objects" not in panel.viewer.layers and tuple(panel.viewer.layers[0].scale)[0] == 1.0,
          "labels removed and layer rescaled")
    check("stale" in d.status(), "user told the analysis was cleared")

    print("Import round trip")
    dialog_paths["open"] = str(report)
    d.click("importAnalysisButton")
    d.wait("currentAnalysis !== null", what="import")
    check(bridge.calibration.z == 0.5 and bridge.last_analysis["summary"]["count"] == count,
          "import restored calibration and the same object count")
    check(d.js("return document.getElementById('voxelZ').value;") == "0.5", "inputs show imported calibration")
    bad = TMP / "bad.md"
    bad.write_text(report.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": 7'), encoding="utf-8")
    dialog_paths["open"] = str(bad)
    d.click("importAnalysisButton")
    d.wait("document.getElementById('appStatus').classList.contains('error')", what="bad import")
    check("version" in d.status().lower(), f"bad file rejected: {d.status()}")

    print("Theme and settings")
    d.set_value("themeSelect", "Light")
    d.js("document.getElementById('themeSelect').dispatchEvent(new Event('change'));")
    d.wait("document.documentElement.dataset.theme === 'light'")
    check(panel.viewer.theme == "light", "light theme applied to web UI and napari")
    d.click("sidebarToggle")
    d.shot(shots, "10_light_input")
    d.click("dataNav")
    d.shot(shots, "11_light_results")
    d.click("sidebarToggle")
    d.click("settingsButton")
    d.js("document.querySelector('#categorySettings input[name=objects]').checked = false;")
    d.click("saveSettings")
    d.wait("document.querySelectorAll('.category-tab').length === 4", what="settings")
    check(True, "hiding a category in settings removes its tab")
    d.click("settingsButton")
    d.js("document.querySelector('#categorySettings input[name=objects]').checked = true;")
    d.click("saveSettings")
    d.wait("document.querySelectorAll('.category-tab').length === 5")

    print("Narrow width")
    window.resize(1100, 760)
    sizes = window.centralWidget().sizes()
    window.centralWidget().setSizes([520, sum(sizes) - 520])
    d.pause(0.5)
    overflow = d.js("return document.documentElement.scrollWidth > window.innerWidth + 1;")
    check(not overflow, "no horizontal page overflow at narrow width")
    d.shot(shots, "12_narrow_results")
    d.click("mainNav")
    d.shot(shots, "13_narrow_input")
    d.set_value("themeSelect", "Dark")
    d.js("document.getElementById('themeSelect').dispatchEvent(new Event('change'));")
    window.resize(1500, 920)

    if args.cells3d:
        print("cells3d (download on first use)")
        d.set_value("sampleSelect", "cells3d")
        d.click("loadSampleButton")
        d.wait("currentStack?.key === 'cells3d' || document.getElementById('appStatus').classList.contains('error')",
               timeout=300, what="cells3d")
        if bridge.sample_key != "cells3d":
            print(f"  ! cells3d could not be loaded: {d.status()}")
        else:
            check(list(bridge.stack.channels) == ["nuclei", "membranes"], "cells3d has nuclei + membranes")
            d.submit("analysisForm")
            d.wait("currentAnalysis !== null", timeout=300, what="cells3d analysis")
            check(True, f"cells3d nuclei: {bridge.last_analysis['summary']['count']} objects")
            d.shot(shots, "14_cells3d")

    print("Clear")
    d.click("clearStackButton")
    d.wait("currentStack === null")
    check(len(panel.viewer.layers) == 0, "napari layers cleared")
    errors = d.js("return window.__errors;")
    check(errors == [], f"no JavaScript errors ({errors})")
    window.close()
    print("End-to-end test passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells3d", action="store_true")
    parser.add_argument("--screenshots")
    sys.exit(run(parser.parse_args()))
