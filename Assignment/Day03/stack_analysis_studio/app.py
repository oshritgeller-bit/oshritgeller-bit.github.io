from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

from PySide6.QtCore import QObject, QSettings, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QSplitter

from napari_panel import NapariPanel
from reporting import build_analysis_markdown, read_analysis_markdown, write_objects_csv
from stack_analysis import (
    SAMPLES,
    THRESHOLD_METHODS,
    UNITS,
    Calibration,
    StackData,
    calibration_summary,
    object_stats,
    objects_per_slice,
    segment_3d,
    summarize_objects,
    z_profile,
)


APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
SAMPLE_LABELS = {
    "cells3d": "cells3d · fluorescence cells (download on first use)",
    "synthetic": "Synthetic blobs · offline",
}
DEFAULT_SETTINGS = {
    "method": "otsu",
    "manualThreshold": None,
    "sigma": 0.5,
    "minVolume": 5.0,
    "splitTouching": True,
    "minDistance": 2.0,
}


class BridgeObject(QObject):
    """Slots called from JavaScript; each returns a JSON string."""

    systemThemeChanged = Signal(str)
    sliceChanged = Signal(int)

    def __init__(self, owner: AnalysisBridge, parent: QObject) -> None:
        super().__init__(parent)
        self.owner = owner

    @Slot(result=str)
    def getInitialState(self) -> str:
        return self.owner._json(self.owner.initial_state())

    @Slot(str, result=str)
    def loadSample(self, key: str) -> str:
        return self.owner._json(self.owner.loadSample(key))

    @Slot(result=str)
    def clearStack(self) -> str:
        return self.owner._json(self.owner.clearStack())

    @Slot(str, result=str)
    def setCalibration(self, calibration_json: str) -> str:
        return self.owner._json(self.owner.setCalibration(calibration_json))

    @Slot(str, result=str)
    def setDisplay(self, display_json: str) -> str:
        return self.owner._json(self.owner.setDisplay(display_json))

    @Slot(int, result=str)
    def goToSlice(self, index: int) -> str:
        return self.owner._json(self.owner.goToSlice(index))

    @Slot(result=str)
    def resetView(self) -> str:
        return self.owner._json(self.owner.resetView())

    @Slot(str, result=str)
    def analyzeStack(self, settings_json: str) -> str:
        return self.owner._json(self.owner.analyzeStack(settings_json))

    @Slot(result=str)
    def saveScreenshot(self) -> str:
        return self.owner._json(self.owner.saveScreenshot())

    @Slot(result=str)
    def openAnalysis(self) -> str:
        return self.owner._json(self.owner.openAnalysis())

    @Slot(str, result=str)
    def exportAnalysis(self, name: str) -> str:
        return self.owner._json(self.owner.exportAnalysis(name))

    @Slot(str, result=str)
    def exportObjectsCsv(self, name: str) -> str:
        return self.owner._json(self.owner.exportObjectsCsv(name))

    @Slot(str, result=str)
    def saveSettings(self, settings_json: str) -> str:
        return self.owner._json(self.owner.saveSettings(settings_json))

    @Slot(str, result=str)
    def setTheme(self, mode: str) -> str:
        return self.owner._json(self.owner.setTheme(mode))

    @Slot(str, result=str)
    def openReference(self, reference: str) -> str:
        return self.owner._json(self.owner.openReference(reference))

    @Slot(result=bool)
    def toggleMaximized(self) -> bool:
        return self.owner.window.toggleMaximized()

    @Slot(result=bool)
    def toggleFullScreen(self) -> bool:
        return self.owner.window.toggleFullScreen()


class AnalysisBridge:
    """Application state and the Python operations exposed to the web interface."""

    def __init__(self, window: StackAnalyzerWindow) -> None:
        self.window = window
        self.settings = window.settings
        self.panel = window.napari_panel
        self.stack: StackData | None = None
        self.sample_key = ""
        self.calibration: Calibration | None = None
        self.last_analysis: dict[str, object] | None = None
        self.bridge_object = BridgeObject(self, window)
        self.panel.zChanged.connect(self.bridge_object.sliceChanged)
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            self._on_system_color_scheme_changed
        )

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _parse_object(raw: str, what: str) -> dict:
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid {what}: {exc.msg}") from exc
        if not isinstance(values, dict):
            raise ValueError(f"{what.capitalize()} must be an object.")
        return values

    def initial_state(self) -> dict[str, object]:
        categories = {
            key: self._setting_enabled(f"categories/{key}", True)
            for key in StackAnalyzerWindow.CATEGORY_LABELS
        }
        categories["overview"] = True
        return {
            "themeMode": self.settings.value("theme_mode", "Dark"),
            "effectiveTheme": self._effective_theme(),
            "fontSize": self._font_size(),
            "startMaximized": self._setting_enabled("start_maximized", True),
            "fullScreen": self._setting_enabled("full_screen", False),
            "enableReferenceLinks": self._setting_enabled("enable_reference_links", False),
            "categories": categories,
            "categoryLabels": StackAnalyzerWindow.CATEGORY_LABELS,
            "samples": [{"key": key, "label": SAMPLE_LABELS[key]} for key in SAMPLES],
            "units": list(UNITS),
            "methods": list(THRESHOLD_METHODS),
            "defaults": DEFAULT_SETTINGS,
        }

    # ------------------------------------------------------------------ stack

    def _stack_payload(self) -> dict[str, object]:
        assert self.stack is not None and self.calibration is not None
        return {
            "key": self.sample_key,
            "name": self.stack.name,
            "source": self.stack.source,
            "shape": list(self.stack.shape),
            "channels": list(self.stack.channels),
            "defaultChannel": self.stack.default_channel,
            "calibration": calibration_summary(self.stack, self.calibration),
            "originalCalibration": self.stack.calibration.to_dict(),
            "currentSlice": self.panel.current_slice,
            "display": self._display_state(),
        }

    def _display_state(self) -> dict[str, object]:
        return {
            "scaleBar": self.panel.scale_bar_visible,
            "ndisplay": int(self.panel.viewer.dims.ndisplay),
        }

    def loadSample(self, key: str) -> dict[str, object]:
        loader = SAMPLES.get(key)
        if loader is None:
            return {"ok": False, "error": "Choose one of the listed samples."}
        try:
            stack = loader()
        except (RuntimeError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self.stack = stack
        self.sample_key = key
        self.calibration = stack.calibration
        self.last_analysis = None
        self.panel.show_stack(stack, self.calibration)
        return {"ok": True, "stack": self._stack_payload()}

    def clearStack(self) -> dict[str, object]:
        had_stack = self.stack is not None
        self.stack = None
        self.sample_key = ""
        self.calibration = None
        self.last_analysis = None
        self.panel.clear()
        return {"ok": True, "cleared": had_stack}

    def setCalibration(self, calibration_json: str) -> dict[str, object]:
        if self.stack is None:
            return {"ok": False, "error": "Load a sample before changing its calibration."}
        try:
            values = self._parse_object(calibration_json, "calibration")
            calibration = Calibration(
                self._number(values.get("z"), "Voxel size z"),
                self._number(values.get("y"), "Voxel size y"),
                self._number(values.get("x"), "Voxel size x"),
                str(values.get("unit", "")),
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        # Physical values (volumes, depths) in an old analysis would be stale.
        cleared = self.last_analysis is not None
        self.calibration = calibration
        self.last_analysis = None
        self.panel.set_calibration(calibration)
        return {"ok": True, "stack": self._stack_payload(), "analysisCleared": cleared}

    def setDisplay(self, display_json: str) -> dict[str, object]:
        if self.stack is None:
            return {"ok": False, "error": "Load a sample first."}
        try:
            values = self._parse_object(display_json, "display settings")
            if "scaleBar" in values:
                if not isinstance(values["scaleBar"], bool):
                    raise ValueError("Scale bar must be on or off.")
                self.panel.set_scale_bar_visible(values["scaleBar"])
            if "ndisplay" in values:
                if values["ndisplay"] not in (2, 3) or isinstance(values["ndisplay"], bool):
                    raise ValueError("Display must be 2D or 3D.")
                self.panel.set_ndisplay(values["ndisplay"])
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "display": self._display_state()}
        return {"ok": True, "display": self._display_state()}

    def goToSlice(self, index: int) -> dict[str, object]:
        if self.stack is None:
            return {"ok": False, "error": "Load a sample first."}
        return {"ok": True, "slice": self.panel.go_to_slice(index)}

    def resetView(self) -> dict[str, object]:
        if self.stack is None:
            return {"ok": False, "error": "Load a sample first."}
        self.panel.reset_view()
        return {"ok": True}

    # --------------------------------------------------------------- analysis

    @staticmethod
    def _number(value: object, label: str, allow_zero: bool = False) -> float:
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
            try:
                value = float(value)
            except ValueError:
                raise ValueError(f"{label} must be a number.") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{label} must be a number.")
        if value < 0 or (value == 0 and not allow_zero):
            raise ValueError(f"{label} must be {'zero or ' if allow_zero else ''}positive.")
        return float(value)

    def _parse_analysis_settings(self, values: dict) -> dict[str, object]:
        method = values.get("method", "otsu")
        if method not in THRESHOLD_METHODS:
            raise ValueError("Choose Otsu, Li or manual thresholding.")
        manual = None
        if method == "manual":
            raw = values.get("manualThreshold")
            if raw in (None, ""):
                raise ValueError("Enter a manual threshold value.")
            if isinstance(raw, str):
                try:
                    raw = float(raw.strip())
                except ValueError:
                    raise ValueError("Manual threshold must be a number.") from None
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                raise ValueError("Manual threshold must be a number.")
            manual = float(raw)
        split = values.get("splitTouching", True)
        if not isinstance(split, bool):
            raise ValueError("Split touching objects must be true or false.")
        return {
            "method": method,
            "manualThreshold": manual,
            "sigma": self._number(values.get("sigma", 0.5), "Smoothing sigma", allow_zero=True),
            "minVolume": self._number(values.get("minVolume", 5), "Minimum volume", allow_zero=True),
            "splitTouching": split,
            "minDistance": self._number(values.get("minDistance", 2), "Minimum distance"),
        }

    def analyzeStack(self, settings_json: str) -> dict[str, object]:
        if self.stack is None or self.calibration is None:
            return {"ok": False, "error": "Load a sample before analysing it."}
        try:
            values = self._parse_object(settings_json, "analysis settings")
            channel = values.get("channel") or self.stack.default_channel
            if channel not in self.stack.channels:
                raise ValueError("Choose one of the stack's channels.")
            settings = self._parse_analysis_settings(values)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return self._run_analysis(channel, settings)

    def _run_analysis(self, channel: str, settings: dict[str, object]) -> dict[str, object]:
        assert self.stack is not None and self.calibration is not None
        volume = self.stack.channels[channel]
        cal = self.calibration
        try:
            profile = z_profile(volume, cal)
            labels, info = segment_3d(
                volume,
                cal,
                method=settings["method"],
                manual_threshold=settings["manualThreshold"],
                sigma=settings["sigma"],
                min_volume=settings["minVolume"],
                split_touching=settings["splitTouching"],
                min_distance=settings["minDistance"],
            )
            objects = object_stats(labels, volume, cal)
        except (ValueError, MemoryError) as exc:
            return {"ok": False, "error": str(exc)}
        summary = summarize_objects(objects)
        unit = cal.unit

        def fmt(value, suffix="", digits=3):
            return "—" if value is None else f"{value:.{digits}g}{suffix}"

        analysis = {
            "name": f"{self.stack.name} · {channel}",
            "sample": self.sample_key,
            "sampleName": self.stack.name,
            "source": self.stack.source,
            "channel": channel,
            "calibration": calibration_summary(self.stack, cal),
            "settings": settings,
            "zProfile": profile,
            "segmentation": info,
            "objects": objects,
            "summary": summary,
            "objectsPerSlice": objects_per_slice(labels),
            "cards": [
                ["Objects", str(summary["count"])],
                ["Total object volume", fmt(summary["totalVolume"], f" {unit}³", 4)],
                ["Mean equivalent diameter", fmt(summary["meanDiameter"], f" {unit}")],
                ["Brightest slice", f"{profile['brightestSlice']} · z {profile['brightestSlice'] * cal.z:.3g} {unit}"],
                ["Best-focus slice", f"{profile['bestFocusSlice']} · z {profile['bestFocusSlice'] * cal.z:.3g} {unit}"],
                ["Half-intensity depth", fmt(profile["halfIntensityDepth"], f" {unit}") if profile["halfIntensityDepth"] is not None else "Not reached"],
            ],
        }
        self.last_analysis = analysis
        self.panel.set_labels(labels)
        return {"ok": True, "analysis": analysis, "stack": self._stack_payload()}

    # ------------------------------------------------------------------ files

    @staticmethod
    def _safe_name(name: str, fallback: str) -> str:
        return re.sub(r"[^\w.-]+", "_", name.strip(), flags=re.UNICODE).strip("_") or fallback

    def saveScreenshot(self) -> dict[str, object]:
        if self.stack is None:
            return {"ok": False, "error": "Load a sample before saving a screenshot."}
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Save napari screenshot",
            f"{self._safe_name(self.stack.name, 'stack')}_view.png",
            "PNG image (*.png)",
        )
        if not path:
            return {"ok": False, "cancelled": True}
        if Path(path).suffix.lower() != ".png":
            path += ".png"
        try:
            self.panel.save_screenshot(path)
        except (OSError, ValueError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "message": f"Screenshot saved to {path}"}

    def openAnalysis(self) -> dict[str, object]:
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            "Import analysis",
            "",
            "Stack Analysis Studio Markdown (*.md);;All files (*)",
        )
        if not path:
            return {"ok": False, "cancelled": True}
        return self.importAnalysisFile(path)

    def importAnalysisFile(self, path: str) -> dict[str, object]:
        """Validate the file, reload its sample and recalibrate, then re-run the analysis."""
        try:
            imported = read_analysis_markdown(path)
            stored_cal = imported["calibration"]
            calibration = Calibration(
                float(stored_cal["z"]), float(stored_cal["y"]), float(stored_cal["x"]), stored_cal["unit"]
            )
            settings = self._parse_analysis_settings(imported["settings"])
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}
        loaded = self.loadSample(imported["sample"])
        if not loaded.get("ok"):
            return loaded
        assert self.stack is not None
        if imported["channel"] not in self.stack.channels:
            return {"ok": False, "error": f"Channel '{imported['channel']}' is not in this sample."}
        self.calibration = calibration
        self.panel.set_calibration(calibration)
        result = self._run_analysis(imported["channel"], settings)
        if result.get("ok"):
            stored_count = imported["segmentation"].get("objectCount")
            new_count = result["analysis"]["segmentation"]["objectCount"]
            result["importNote"] = (
                f"Re-analysed: {new_count} objects (file: {stored_count})."
                if stored_count != new_count
                else f"Re-analysed and matched the file: {new_count} objects."
            )
        return result

    def exportAnalysis(self, name: str) -> dict[str, object]:
        if self.last_analysis is None:
            return {"ok": False, "error": "Run an analysis before exporting it."}
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Export analysis",
            f"{self._safe_name(name, 'Stack')}.md",
            "Markdown analysis (*.md)",
        )
        if not path:
            return {"ok": False, "cancelled": True}
        if Path(path).suffix.lower() != ".md":
            path += ".md"
        try:
            Path(path).write_text(build_analysis_markdown(self.last_analysis), encoding="utf-8")
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "message": f"Analysis exported to {path}"}

    def exportObjectsCsv(self, name: str) -> dict[str, object]:
        if self.last_analysis is None:
            return {"ok": False, "error": "Run an analysis before exporting objects."}
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Export objects table",
            f"{self._safe_name(name, 'Stack')}_objects.csv",
            "CSV table (*.csv)",
        )
        if not path:
            return {"ok": False, "cancelled": True}
        if Path(path).suffix.lower() != ".csv":
            path += ".csv"
        try:
            write_objects_csv(path, self.last_analysis)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "message": f"Objects exported to {path}"}

    # --------------------------------------------------------------- settings

    def saveSettings(self, settings_json: str) -> dict[str, object]:
        try:
            values = self._parse_object(settings_json, "settings")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        font_size = values.get("fontSize", self._font_size())
        if type(font_size) is not int or not 8 <= font_size <= 24:
            return {"ok": False, "error": "Font size must be from 8 to 24."}
        start_maximized = values.get("startMaximized", True)
        if not isinstance(start_maximized, bool):
            return {"ok": False, "error": "Startup window preference must be true or false."}
        full_screen = values.get("fullScreen", False)
        if not isinstance(full_screen, bool):
            return {"ok": False, "error": "Full-screen preference must be true or false."}
        enable_reference_links = values.get(
            "enableReferenceLinks", self._setting_enabled("enable_reference_links", False)
        )
        if not isinstance(enable_reference_links, bool):
            return {"ok": False, "error": "Reference links must be true or false."}
        categories = values.get("categories", {})
        if not isinstance(categories, dict):
            return {"ok": False, "error": "Result categories must be an object."}
        if any(
            key not in StackAnalyzerWindow.CATEGORY_LABELS or not isinstance(enabled, bool)
            for key, enabled in categories.items()
        ):
            return {"ok": False, "error": "Each result category must have a valid boolean setting."}
        self.settings.setValue("font_size", font_size)
        self.settings.setValue("start_maximized", start_maximized)
        self.settings.setValue("full_screen", full_screen)
        self.settings.setValue("enable_reference_links", enable_reference_links)
        for key in StackAnalyzerWindow.CATEGORY_LABELS:
            if key != "overview" and key in categories:
                self.settings.setValue(f"categories/{key}", bool(categories[key]))
        self.window.applyWindowMode(full_screen, start_maximized)
        return {"ok": True, "settings": self.initial_state()}

    def openReference(self, reference: str) -> dict[str, object]:
        if not self._setting_enabled("enable_reference_links", False):
            return {"ok": False, "error": "Enable reference links in Settings first."}
        references = {
            "napari": "https://napari.org/stable/",
            "cells3d": "https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.cells3d",
            "laplace": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.laplace.html",
            "otsu": "https://scikit-image.org/docs/stable/api/skimage.filters.html#skimage.filters.threshold_otsu",
            "li": "https://scikit-image.org/docs/stable/api/skimage.filters.html#skimage.filters.threshold_li",
            "watershed": "https://scikit-image.org/docs/stable/api/skimage.segmentation.html#skimage.segmentation.watershed",
            "marching-cubes": "https://scikit-image.org/docs/stable/api/skimage.measure.html#skimage.measure.marching_cubes",
        }
        url = references.get(reference)
        if url is None:
            return {"ok": False, "error": "This reference is not available."}
        if not QDesktopServices.openUrl(QUrl(url)):
            return {"ok": False, "error": "The reference could not be opened in a browser."}
        return {"ok": True}

    def setTheme(self, mode: str) -> dict[str, object]:
        if mode not in {"Dark", "Light", "System"}:
            return {"ok": False, "error": "Choose Dark, Light, or System theme."}
        self.settings.setValue("theme_mode", mode)
        effective = self._effective_theme()
        self.panel.set_theme(effective)
        return {"ok": True, "themeMode": mode, "effectiveTheme": effective}

    def _setting_enabled(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _font_size(self) -> int:
        try:
            size = int(self.settings.value("font_size", "12"))
        except (TypeError, ValueError):
            return 12
        return min(24, max(8, size))

    def _effective_theme(self) -> str:
        mode = self.settings.value("theme_mode", "Dark")
        if mode in {"Dark", "Light"}:
            return str(mode).lower()
        scheme = QApplication.instance().styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "light"
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        window_color = QApplication.instance().palette().window().color()
        return "dark" if window_color.lightness() < 128 else "light"

    def _on_system_color_scheme_changed(self, _scheme) -> None:
        if self.settings.value("theme_mode", "Dark") == "System":
            theme = self._effective_theme()
            self.panel.set_theme(theme)
            self.bridge_object.systemThemeChanged.emit(theme)


class StackAnalyzerWindow(QMainWindow):
    CATEGORY_LABELS = {
        "overview": "Overview",
        "zprofile": "Z-axis profile",
        "calibration": "Calibration",
        "segmentation": "Segmentation",
        "objects": "Objects",
    }

    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("StackAnalysisStudio", "Desktop")
        self.setWindowTitle("Stack Analysis Studio")
        self.setWindowIcon(QIcon(str(APP_ROOT / "app_icon.svg")))
        self.resize(1600, 960)
        self.setMinimumSize(1100, 680)

        # Web UI on the left, embedded napari on the right.
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.web_view = QWebEngineView(splitter)
        self.napari_panel = NapariPanel(splitter)
        splitter.addWidget(self.web_view)
        splitter.addWidget(self.napari_panel)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes([720, 880])
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

        self.bridge = AnalysisBridge(self)
        self.napari_panel.set_theme(self.bridge._effective_theme())
        # The channel is parented to the page it serves; never swap the page out
        # from under it (that crashes Qt WebEngine).
        page = self.web_view.page()
        self.web_channel = QWebChannel(page)
        self.web_channel.registerObject("bridge", self.bridge.bridge_object)
        page.setWebChannel(self.web_channel)
        page_path = (APP_ROOT / "web" / "index.html").resolve()
        self.web_view.load(QUrl.fromLocalFile(str(page_path)))

        shortcut = QShortcut(QKeySequence("F11"), self)
        shortcut.activated.connect(self.toggleFullScreen)

    def showInitial(self) -> None:
        if self.bridge._setting_enabled("full_screen", False):
            self.showFullScreen()
        elif self.bridge._setting_enabled("start_maximized", True):
            self.showMaximized()
        else:
            self.show()

    def toggleMaximized(self) -> bool:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        return self.isMaximized()

    def toggleFullScreen(self) -> bool:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        return self.isFullScreen()

    def applyWindowMode(self, full_screen: bool, start_maximized: bool) -> None:
        if full_screen:
            self.showFullScreen()
        elif start_maximized:
            self.showMaximized()
        else:
            self.showNormal()


def configure_qt() -> None:
    """Graphics setup that must happen before the QApplication exists."""
    # Both QtWebEngine and napari's OpenGL canvas need shared contexts.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    # napari's canvas is a QOpenGLWidget, so the whole window composites with
    # OpenGL. QWebEngineView is a QQuickWidget that defaults to Metal/Direct3D,
    # and then renders nothing beside napari, so Qt Quick must use OpenGL too.
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)


def main() -> int:
    configure_qt()
    from napari.qt import get_qapp

    # get_qapp registers napari's icon resources (missing icons otherwise).
    app = get_qapp(app_name="Stack Analysis Studio", org_name="StackAnalysisStudio")
    window = StackAnalyzerWindow()
    window.showInitial()
    return app.exec()
