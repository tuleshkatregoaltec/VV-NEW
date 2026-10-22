import json
import math
from typing import Any

from app.plots.models import GISLandUse, Usage


def land_use_labels(raw: Any, limit: int = 3) -> list[str]:
    if raw in (None, "", []):
        return []

    if isinstance(raw, list):
        labels: list[str] = []
        for item in raw:
            labels.extend(land_use_labels(item, limit=limit))
            if len(labels) >= limit:
                break
        return labels[:limit]

    if not isinstance(raw, str):
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = raw.strip()
        return [cleaned] if cleaned else []

    labels = []
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            label = " ".join(
                str(item.get(key) or "").strip()
                for key in ("type", "use")
                if str(item.get(key) or "").strip()
            )
            if label and label not in labels:
                labels.append(label)
            if len(labels) >= limit:
                break
    return labels


def _dubai_local_tm_to_lng_lat(easting: float, northing: float) -> list[float]:
    """Convert Dubai Local Transverse Mercator coordinates to WGS84 lng/lat."""
    semi_major_axis = 6378137.0
    flattening = 1 / 298.257223563
    scale_factor = 1.0
    eccentricity_sq = flattening * (2 - flattening)
    second_eccentricity_sq = eccentricity_sq / (1 - eccentricity_sq)

    x = easting - 500000.0
    y = northing
    central_meridian = math.radians(55.3333333333333)

    meridional_arc = y / scale_factor
    mu = meridional_arc / (
        semi_major_axis
        * (1 - eccentricity_sq / 4 - 3 * eccentricity_sq**2 / 64 - 5 * eccentricity_sq**3 / 256)
    )

    e1 = (1 - math.sqrt(1 - eccentricity_sq)) / (1 + math.sqrt(1 - eccentricity_sq))
    footprint_lat = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )

    sin_fp = math.sin(footprint_lat)
    cos_fp = math.cos(footprint_lat)
    tan_fp = math.tan(footprint_lat)

    c1 = second_eccentricity_sq * cos_fp**2
    t1 = tan_fp**2
    n1 = semi_major_axis / math.sqrt(1 - eccentricity_sq * sin_fp**2)
    r1 = semi_major_axis * (1 - eccentricity_sq) / (1 - eccentricity_sq * sin_fp**2) ** 1.5
    d = x / (n1 * scale_factor)

    lat = footprint_lat - (n1 * tan_fp / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * second_eccentricity_sq) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * second_eccentricity_sq - 3 * c1**2)
        * d**6
        / 720
    )
    lon = (
        central_meridian
        + (
            d
            - (1 + 2 * t1 + c1) * d**3 / 6
            + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * second_eccentricity_sq + 24 * t1**2)
            * d**5
            / 120
        )
        / cos_fp
    )

    return [math.degrees(lon), math.degrees(lat)]


def _normalize_plot_coordinate(pair: Any) -> list[float] | None:
    if not isinstance(pair, list) or len(pair) != 2:
        return None
    try:
        x = float(pair[0])
        y = float(pair[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None

    if -180 <= x <= 180 and -90 <= y <= 90:
        return [x, y]
    if 100000 <= x <= 900000 and 0 <= y <= 10000000:
        return _dubai_local_tm_to_lng_lat(x, y)
    return None


def _parse_plot_coordinates(raw: str) -> list[list[float]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        coords = [
            coordinate for p in parsed if (coordinate := _normalize_plot_coordinate(p)) is not None
        ]
        if len(coords) >= 3 and coords[0] != coords[-1]:
            coords.append(coords[0])
        return coords
    except Exception:
        return []


def _coordinate_bounds(coordinates: list[list[float]]) -> tuple[float, float, float, float]:
    lngs = [point[0] for point in coordinates]
    lats = [point[1] for point in coordinates]
    return min(lngs), min(lats), max(lngs), max(lats)


def _bounds_intersects(
    plot_bounds: tuple[float, float, float, float],
    *,
    west: float,
    south: float,
    east: float,
    north: float,
) -> bool:
    plot_west, plot_south, plot_east, plot_north = plot_bounds
    return plot_west <= east and plot_east >= west and plot_south <= north and plot_north >= south


def _convex_hull(points: list[list[float]]) -> list[list[float]]:
    unique_points = sorted({(round(point[0], 8), round(point[1], 8)) for point in points})
    if len(unique_points) < 3:
        return []

    def cross(
        origin: tuple[float, float],
        point_a: tuple[float, float],
        point_b: tuple[float, float],
    ) -> float:
        return (point_a[0] - origin[0]) * (point_b[1] - origin[1]) - (point_a[1] - origin[1]) * (
            point_b[0] - origin[0]
        )

    lower: list[tuple[float, float]] = []
    for point in unique_points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[tuple[float, float]] = []
    for point in reversed(unique_points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    hull = [[lng, lat] for lng, lat in lower[:-1] + upper[:-1]]
    if len(hull) >= 3:
        hull.append(hull[0])
    return hull


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def infer_usage_from_plot(
    land_use_values: list[str], gfa_type: str, general_notes: list[str]
) -> Usage:
    haystack = " ".join(
        [
            *(land_use_values or []),
            gfa_type or "",
            *(general_notes or []),
        ]
    ).lower()
    has_res = any(
        token in haystack
        for token in ["residential", "villa", "apartment", "townhouse", "dwelling"]
    )
    has_com = any(
        token in haystack
        for token in [
            "commercial",
            "office",
            "retail",
            "shopping center",
            "shop",
            "hotel",
            "hospitality",
            "warehouse",
            "industrial",
        ]
    )
    if has_res and has_com:
        return "mixed_use"
    if has_res:
        return "residential"
    if has_com:
        return "commercial"
    return "unknown"


def _parse_land_use(raw: str) -> list[GISLandUse]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return [
            GISLandUse(
                type=str(item.get("type", "")).strip(),
                use=str(item.get("use", "")).strip(),
            )
            for item in parsed
            if item.get("type") or item.get("use")
        ]
    except Exception:
        return []


def _parse_notes(raw: str) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(" | ") if part.strip()]


def _coordinate_pair(raw: object) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        x = float(raw[0])  # type: ignore[index]
        y = float(raw[1])  # type: ignore[index]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return [x, y]


def _extract_coordinate_pairs(raw: object) -> list[list[float]]:
    pair = _coordinate_pair(raw)
    if pair is not None:
        return [pair]
    if not isinstance(raw, (list, tuple)):
        return []
    coords: list[list[float]] = []
    for item in raw:
        coords.extend(_extract_coordinate_pairs(item))
    return coords


def _parse_coordinates(raw: object) -> list[list[float]]:
    if raw in (None, "", "[]"):
        return []
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if isinstance(parsed, dict):
        parsed = parsed.get("coordinates", [])
    coords = _extract_coordinate_pairs(parsed)
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def _build_setbacks(row: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for side in range(1, 5):
        building = str(row.get(f"side{side}_building") or "").strip()
        podium = str(row.get(f"side{side}_podium") or "").strip()
        if building:
            result[f"side{side}_building"] = building
        if podium:
            result[f"side{side}_podium"] = podium
    return result


def _build_plot_warnings(row: dict[str, Any], far: float | None, notes: list[str]) -> list[str]:
    warnings: list[str] = []
    if not row.get("is_verified"):
        warnings.append("GIS plot is not marked as verified.")
    if not row.get("max_gfa_sqm"):
        warnings.append("Maximum GFA is missing, so HBU fit is lower confidence.")
    if row.get("site_plan_expiry_date"):
        warnings.append(f"Site plan expiry date: {row['site_plan_expiry_date']}.")
    if row.get("max_height", "").upper() == "SEE NOTES":
        warnings.append("Maximum height is defined in notes instead of a structured field.")
    if row.get("max_coverage", "").upper() == "SEE NOTES":
        warnings.append("Maximum coverage is defined in notes instead of a structured field.")
    if far and far > 12:
        warnings.append("Derived FAR is unusually high; confirm the GIS record manually.")
    if any("NOC" in note.upper() for note in notes):
        warnings.append("General notes include authority or third-party NOC requirements.")
    return warnings


def normalize_whitespace(value: str) -> str:
    return " ".join((value or "").strip().split())
