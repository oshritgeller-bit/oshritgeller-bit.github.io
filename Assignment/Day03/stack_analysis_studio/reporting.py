from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Dict

from stack_analysis import SAMPLES, THRESHOLD_METHODS, UNITS


METHOD_NOTES = [
    "Calibration: every physical value uses the voxel size (z, y, x) shown in the Calibration section. Changing the voxel size changes volumes, areas and depths.",
    "Z-axis profile: mean, maximum and standard deviation are computed per slice on raw intensities. Foreground % and SNR use one Otsu threshold for the whole stack; SNR = (mean foreground − mean background) / background standard deviation.",
    "Focus is the variance of the 2D Laplacian of each slice. It is a relative measure and also rises with noise.",
    "Half-intensity depth is the distance from the brightest slice to the first deeper slice whose mean falls below half-way between the dimmest and brightest slice means.",
    "Segmentation: Gaussian smoothing (sigma given in physical units and converted per axis to voxels), Otsu, Li or manual threshold, 26-connected labelling, minimum-volume filter, and optional watershed on the physical distance transform to split touching objects.",
    "Surface area uses marching cubes on a Gaussian-blurred mask (sigma ≈ 0.75 × smallest voxel edge); measured on spheres it is within ~2 % at a radius of 10 voxels but underestimated by 30–55 % below 3 voxels, so sphericity of very small objects is unreliable (capped at 1).",
    "Objects touching the stack border are truncated. No deconvolution or PSF correction is applied, so sizes are estimates. Classical thresholding and watershed can over- or under-split touching or irregular objects.",
]

ANALYSIS_DATA_START = "<!-- STACK_ANALYSIS_STUDIO_DATA_START -->"
ANALYSIS_DATA_END = "<!-- STACK_ANALYSIS_STUDIO_DATA_END -->"

OBJECT_COLUMNS = [
    ("label", "Label"),
    ("voxels", "Voxels"),
    ("volume", "Volume ({unit}³)"),
    ("surfaceArea", "Surface area ({unit}²)"),
    ("equivalentDiameter", "Equivalent diameter ({unit})"),
    ("sphericity", "Sphericity"),
    ("zStart", "First slice"),
    ("zEnd", "Last slice"),
    ("zExtent", "Z extent ({unit})"),
    ("centroidZ", "Centroid z ({unit})"),
    ("centroidY", "Centroid y ({unit})"),
    ("centroidX", "Centroid x ({unit})"),
    ("meanIntensity", "Mean intensity"),
]


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _row(values) -> str:
    return "| " + " | ".join(_fmt(value) for value in values) + " |"


def build_analysis_markdown(analysis: Dict[str, object]) -> str:
    """Create a readable Markdown report with an embedded round-trip payload."""
    validate_analysis(analysis)
    cal = analysis["calibration"]
    unit = cal["unit"]
    settings = analysis["settings"]
    segmentation = analysis["segmentation"]
    summary = analysis["summary"]
    profile = analysis["zProfile"]
    lines = [
        f"# {analysis['name']}",
        "",
        "**Stack Analysis Studio · 3D stack analysis**",
        "",
        f"**Source:** {analysis.get('source', '')}  ",
        f"**Channel:** {analysis['channel']} · **Shape (z, y, x):** {' × '.join(str(n) for n in cal['shape'])}",
        "",
        "## Calibration",
        "",
        "| Property | Value |",
        "|:--|:--|",
        _row(["Voxel size z", f"{cal['z']} {unit}"]),
        _row(["Voxel size y", f"{cal['y']} {unit}"]),
        _row(["Voxel size x", f"{cal['x']} {unit}"]),
        _row(["Voxel volume", f"{_fmt(cal['voxelVolume'])} {unit}³"]),
        _row(["Anisotropy (z / finest lateral)", cal["anisotropy"]]),
        _row(["Stack extent (z × y × x)", " × ".join(f"{_fmt(e)}" for e in cal["extent"]) + f" {unit}"]),
        _row(["Scale bar length", f"{_fmt(cal['scaleBarLength'])} {unit}"]),
        "",
        "## Z-axis profile",
        "",
        "| Property | Value |",
        "|:--|:--|",
        _row(["Stack Otsu threshold", profile["threshold"]]),
        _row(["Brightest slice", profile["brightestSlice"]]),
        _row(["Best-focus slice", profile["bestFocusSlice"]]),
        _row([
            "Half-intensity depth",
            f"{_fmt(profile['halfIntensityDepth'])} {unit}" if profile["halfIntensityDepth"] is not None else "Not reached",
        ]),
        "",
        f"| Slice | z ({unit}) | Mean | Max | Std | Focus | Foreground % | SNR | Objects |",
        "|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    per_slice = analysis["objectsPerSlice"]
    for item in profile["slices"]:
        lines.append(_row([
            item["index"], item["z"], item["mean"], item["max"], item["std"],
            item["focus"], item["foregroundPercent"], item["snr"], per_slice[item["index"]],
        ]))
    lines.extend([
        "",
        "## Segmentation",
        "",
        "| Parameter | Value |",
        "|:--|:--|",
        _row(["Threshold method", str(settings["method"]).title()]),
        _row(["Threshold value (smoothed image)", segmentation["threshold"]]),
        _row(["Gaussian sigma", f"{_fmt(settings['sigma'])} {unit}"]),
        _row(["Minimum object volume", f"{_fmt(settings['minVolume'])} {unit}³ ({segmentation['minVoxels']} voxels)"]),
        _row(["Split touching objects", "Yes" if settings["splitTouching"] else "No"]),
        _row(["Watershed minimum distance", f"{_fmt(settings['minDistance'])} {unit}"]),
        _row(["Objects found", segmentation["objectCount"]]),
        _row(["Removed as too small", segmentation["removedSmall"]]),
        _row(["Foreground (final objects)", f"{_fmt(segmentation['foregroundPercent'])} %"]),
        "",
        "## Objects",
        "",
        "| Summary | Value |",
        "|:--|:--|",
        _row(["Object count", summary["count"]]),
        _row(["Total volume", f"{_fmt(summary['totalVolume'])} {unit}³"]),
        _row(["Mean volume", f"{_fmt(summary['meanVolume'])} {unit}³"]),
        _row(["Median volume", f"{_fmt(summary['medianVolume'])} {unit}³"]),
        _row(["Mean equivalent diameter", f"{_fmt(summary['meanDiameter'])} {unit}"]),
        _row(["Mean sphericity", summary["meanSphericity"]]),
        "",
    ])
    if analysis["objects"]:
        lines.append(_row([title.format(unit=unit) for _key, title in OBJECT_COLUMNS]))
        lines.append("|" + "--:|" * len(OBJECT_COLUMNS))
        for item in analysis["objects"]:
            lines.append(_row([item[key] for key, _title in OBJECT_COLUMNS]))
    else:
        lines.append("No objects were found with these settings.")
    lines.extend(["", "## Methods and limitations", ""])
    lines.extend(f"- {note}" for note in METHOD_NOTES)
    lines.extend([
        "- This report embeds its analysis data so it can be imported back into Stack Analysis Studio.",
        "",
        ANALYSIS_DATA_START,
        "```json",
        json.dumps({"schema_version": 1, "analysis": analysis}, indent=2, ensure_ascii=False, allow_nan=False),
        "```",
        ANALYSIS_DATA_END,
        "",
    ])
    return "\n".join(lines)


def _positive(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _non_negative(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def validate_analysis(analysis: object) -> Dict[str, object]:
    """Check the fields needed to rebuild an analysis; raise ValueError if malformed."""
    if not isinstance(analysis, dict):
        raise ValueError("The file does not contain a valid analysis.")
    for key in ("name", "sample", "channel"):
        if not isinstance(analysis.get(key), str) or not analysis[key]:
            raise ValueError(f"The analysis field '{key}' is missing.")
    if analysis["sample"] not in SAMPLES:
        raise ValueError(f"Unknown sample '{analysis['sample']}'.")
    cal = analysis.get("calibration")
    if (
        not isinstance(cal, dict)
        or not all(_positive(cal.get(axis)) for axis in ("z", "y", "x"))
        or cal.get("unit") not in UNITS
    ):
        raise ValueError("The calibration in this file is invalid.")
    settings = analysis.get("settings")
    if (
        not isinstance(settings, dict)
        or settings.get("method") not in THRESHOLD_METHODS
        or not _non_negative(settings.get("sigma"))
        or not _non_negative(settings.get("minVolume"))
        or not _non_negative(settings.get("minDistance"))
        or not isinstance(settings.get("splitTouching"), bool)
    ):
        raise ValueError("The analysis settings in this file are invalid.")
    if settings["method"] == "manual" and not (
        isinstance(settings.get("manualThreshold"), (int, float))
        and not isinstance(settings.get("manualThreshold"), bool)
        and math.isfinite(settings["manualThreshold"])
    ):
        raise ValueError("The manual threshold in this file is invalid.")
    for key, kind in (
        ("zProfile", dict),
        ("segmentation", dict),
        ("summary", dict),
        ("objects", list),
        ("objectsPerSlice", list),
    ):
        if not isinstance(analysis.get(key), kind):
            raise ValueError(f"The analysis section '{key}' is missing or malformed.")
    return analysis


def read_analysis_markdown(path: str | Path) -> Dict[str, object]:
    text = Path(path).read_text(encoding="utf-8-sig")
    start = text.find(ANALYSIS_DATA_START)
    end = text.find(ANALYSIS_DATA_END, start + len(ANALYSIS_DATA_START))
    if start < 0 or end < 0:
        raise ValueError("This Markdown file has no embedded analysis data.")
    block = text[start + len(ANALYSIS_DATA_START) : end].strip()
    if not block.startswith("```json") or not block.endswith("```"):
        raise ValueError("The embedded analysis data block is malformed.")
    try:
        payload = json.loads(block[len("```json") : -3].strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid embedded analysis data: {exc.msg}") from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
    ):
        raise ValueError("Unsupported analysis file version.")
    return validate_analysis(payload.get("analysis"))


def write_objects_csv(path: str | Path, analysis: Dict[str, object]) -> None:
    unit = analysis["calibration"]["unit"]
    with Path(path).open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow([title.format(unit=unit) for _key, title in OBJECT_COLUMNS])
        for item in analysis["objects"]:
            writer.writerow(["" if item[key] is None else item[key] for key, _title in OBJECT_COLUMNS])
