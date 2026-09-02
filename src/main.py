"""
Schwarzplan Generator — Desktop Application

A modern GUI for generating architectural figure-ground diagrams
from OpenStreetMap building data at exact architectural scales.
"""

import os
import re
import sys
import ssl
import json
import math
import time
import asyncio
import tempfile
import traceback
import threading
import subprocess
import urllib.request
import urllib.parse


def _log_dir():
    """A user-writable directory for crash logs, per platform convention."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        candidate = os.path.join(home, "Library", "Logs", "Schwarzplan")
    elif sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        candidate = os.path.join(root, "Schwarzplan", "Logs")
    else:
        root = os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
        candidate = os.path.join(root, "schwarzplan")
    try:
        os.makedirs(candidate, exist_ok=True)
        return candidate
    except Exception:
        return tempfile.gettempdir()


def __log_crash(e):
    try:
        log_path = os.path.join(_log_dir(), "schwarzplan_crash.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(traceback.format_exc())
    except Exception:
        pass

try:
    import flet as ft
    import flet_map as ftm

    from schwarzplan_engine import (
        generate_schwarzplan,
        normalize_hex,
        MAX_QUERY_RADIUS_M,
        PAPER_SIZES,
        SCALE_OPTIONS,
        SUPPORTED_FORMATS,
        USER_AGENT,
        OSM_ATTRIBUTION,
    )
except Exception as e:
    __log_crash(e)
    raise


# Esri's Light/Dark Gray Canvas is published up to zoom 16. Asking for more
# gives blank tiles, so the map, the tile layers and the search all use these.
MAP_MIN_ZOOM = 2
MAP_MAX_ZOOM = 16


def mercator_y(lat):
    """Normalised Web Mercator Y in 0..1, used to measure a latitude span."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    s = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    # Rounding at the clamp limit can land a hair outside the range.
    return max(0.0, min(1.0, y))


def zoom_for_bounds(boundingbox, view_w_px=880, view_h_px=680, tile_px=256,
                    min_zoom=MAP_MIN_ZOOM, max_zoom=MAP_MAX_ZOOM):
    """
    Picks a zoom level that frames a Nominatim bounding box.

    boundingbox is [south, north, west, east] as strings. A fixed zoom shows a
    country as an anonymous field and a monument as a rooftop, so the extent of
    the result decides instead.
    """
    try:
        south, north, west, east = (float(v) for v in boundingbox)
    except (TypeError, ValueError):
        return min(14, max_zoom)

    lon_span = abs(east - west)
    lat_span = abs(mercator_y(north) - mercator_y(south))

    candidates = []
    if lon_span > 0:
        candidates.append(math.log2((view_w_px / tile_px) * 360.0 / lon_span))
    if lat_span > 0:
        candidates.append(math.log2((view_h_px / tile_px) / lat_span))
    if not candidates:
        return max_zoom
    return max(min_zoom, min(max_zoom, int(math.floor(min(candidates)))))


def parse_coordinates(text):
    """
    Reads a pasted "53.5581, 9.9632" into a lat/lon pair, or None.

    Pasting coordinates is the one search that needs no server round-trip.
    """
    parts = re.split(r"[\s,;]+", (text or "").strip())
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return round(lat, 6), round(lon, 6)
    return None


def default_save_dir():
    """Where plans land when the user has not picked a folder."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop) and os.access(desktop, os.W_OK):
        return desktop
    return os.path.expanduser("~")


def with_extension(name, fmt):
    """
    Returns the filename carrying the selected format's extension.

    The format dropdown decides the format, so a name ending in .pdf must not
    silently override a choice of SVG. Any directory part is dropped: a typed
    path separator must not redirect where the file is written.
    """
    stem = os.path.basename((name or "").strip()) or "plan"
    for ext in SUPPORTED_FORMATS:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return f"{stem or 'plan'}.{fmt}"


# ── Color Palettes & Basemaps ──────────────────────────────────────
DARK_PALETTE = {
    "bg_dark": "#0D0D0D",
    "bg_panel": "#161616",
    "bg_card": "#1E1E1E",
    "bg_input": "#252525",
    "text_primary": "#F0F0F0",
    "text_secondary": "#8A8A8A",
    "border_subtle": "#2A2A2A",
    "accent": "#4FC3F7",
    "accent_hover": "#81D4FA",
    "tile_url": "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    "label_url": "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
    # Must name whoever serves tile_url above. Change both together.
    "tile_attribution": "Basemap \u00a9 Esri",
}

LIGHT_PALETTE = {
    "bg_dark": "#F4F6F8",
    "bg_panel": "#FFFFFF",
    "bg_card": "#FFFFFF",
    "bg_input": "#ECEFF1",
    "text_primary": "#1A202C",
    "text_secondary": "#718096",
    "border_subtle": "#E2E8F0",
    "accent": "#0288D1",
    "accent_hover": "#039BE5",
    "tile_url": "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    "label_url": "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
    "tile_attribution": "Basemap \u00a9 Esri",
}

SUCCESS_GREEN = "#66BB6A"
ERROR_RED = "#EF5350"
WARNING_AMBER = "#FFA726"


def calculate_bbox_corners(center_lat: float, center_lon: float, paper_w_mm: float, paper_h_mm: float, margin_mm: float, scale: int):
    """
    Computes geographic coordinates (NW, NE, SE, SW) of the exact printed map area.
    """
    # No clamping here: the preview must describe exactly what the export will
    # produce. Callers reject a border that does not fit before calling.
    map_w_mm = paper_w_mm - 2 * margin_mm
    map_h_mm = paper_h_mm - 2 * margin_mm
    real_w_m = (map_w_mm / 1000.0) * scale
    real_h_m = (map_h_mm / 1000.0) * scale

    lat_rad = math.radians(center_lat)
    # WGS84 metric meters per degree
    m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
    m_per_deg_lon = 111412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)
    if abs(m_per_deg_lon) < 1.0:
        m_per_deg_lon = 1.0

    d_lat = (real_h_m / 2.0) / m_per_deg_lat
    d_lon = (real_w_m / 2.0) / m_per_deg_lon

    nw = ftm.MapLatitudeLongitude(center_lat + d_lat, center_lon - d_lon)
    ne = ftm.MapLatitudeLongitude(center_lat + d_lat, center_lon + d_lon)
    se = ftm.MapLatitudeLongitude(center_lat - d_lat, center_lon + d_lon)
    sw = ftm.MapLatitudeLongitude(center_lat - d_lat, center_lon - d_lon)

    return [nw, ne, se, sw], real_w_m, real_h_m


def main(page: ft.Page):
    # ── Theme & OS Detection ───────────────────────────────────────
    is_os_dark = False
    try:
        if hasattr(page, "platform_brightness") and page.platform_brightness == ft.Brightness.DARK:
            is_os_dark = True
    except Exception:
        pass

    is_dark = [is_os_dark]
    selected_lat = [53.5581]
    selected_lon = [9.9632]
    chosen_save_dir = [None]
    filename_is_custom = [False]
    last_generated_file = [None]

    def current_theme():
        return DARK_PALETTE if is_dark[0] else LIGHT_PALETTE

    initial_pal = current_theme()

    # ── Page Setup ─────────────────────────────────────────────────
    page.title = "Schwarzplan Generator"
    page.bgcolor = initial_pal["bg_dark"]
    page.padding = 0
    page.window.min_width = 1020
    page.window.min_height = 640
    # Kept under 1366x768, the smallest laptop screen still in common use. A
    # taller default opens partly off-screen there.
    page.window.width = 1280
    page.window.height = 740
    page.fonts = {
        "Inter": "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap",
    }
    page.theme = ft.Theme(
        font_family="Inter",
        color_scheme=ft.ColorScheme(
            primary=initial_pal["accent"],
            on_primary="#FFFFFF" if not is_dark[0] else "#000000",
            surface=initial_pal["bg_card"],
            on_surface=initial_pal["text_primary"],
        ),
    )
    page.theme_mode = ft.ThemeMode.DARK if is_dark[0] else ft.ThemeMode.LIGHT

    marker_layer_ref = ft.Ref[ftm.MarkerLayer]()
    polygon_layer_ref = ft.Ref[ftm.PolygonLayer]()
    tile_layer_ref = ft.Ref[ftm.TileLayer]()
    label_layer_ref = ft.Ref[ftm.TileLayer]()
    map_ref = ft.Ref[ftm.Map]()

    # ── Helpers ────────────────────────────────────────────────────
    _pad_field = ft.Padding.symmetric(horizontal=12, vertical=4)

    def styled_text_field(**kwargs):
        pal = current_theme()
        defaults = dict(
            text_size=13,
            label_style=ft.TextStyle(size=11, color=pal["text_secondary"]),
            bgcolor=pal["bg_input"],
            border_color=pal["border_subtle"],
            focused_border_color=pal["accent"],
            color=pal["text_primary"],
            cursor_color=pal["accent"],
            border_radius=8,
            height=46,
            content_padding=_pad_field,
        )
        defaults.update(kwargs)
        return ft.TextField(**defaults)

    def styled_dropdown(**kwargs):
        pal = current_theme()
        defaults = dict(
            text_size=13,
            bgcolor=pal["bg_input"],
            border_color=pal["border_subtle"],
            focused_border_color=pal["accent"],
            color=pal["text_primary"],
            border_radius=8,
            height=46,
            content_padding=_pad_field,
        )
        defaults.update(kwargs)
        return ft.Dropdown(**defaults)

    # ── Section Headers & Labels ───────────────────────────────────
    title_text = ft.Text("Schwarzplan", size=20, weight=ft.FontWeight.W_700, color=initial_pal["text_primary"])
    subtitle_text = ft.Text("Architectural Figure-Ground Generator", size=11, color=initial_pal["text_secondary"])
    app_icon = ft.Icon(ft.Icons.GRID_ON_ROUNDED, color=initial_pal["accent"], size=22)

    coords_header = ft.Text("📍  Center Coordinates", size=13, weight=ft.FontWeight.W_600, color=initial_pal["text_primary"])
    scale_header = ft.Text("⚙️  Scale & Format", size=13, weight=ft.FontWeight.W_600, color=initial_pal["text_primary"])
    context_header = ft.Text("🌊  Urban Context Layers", size=13, weight=ft.FontWeight.W_600, color=initial_pal["text_primary"])
    output_header = ft.Text("📄  Save Output", size=13, weight=ft.FontWeight.W_600, color=initial_pal["text_primary"])

    scale_label = ft.Text("Architectural Scale", size=11, weight=ft.FontWeight.W_500, color=initial_pal["text_secondary"])
    paper_label = ft.Text("Paper Format & Orientation", size=11, weight=ft.FontWeight.W_500, color=initial_pal["text_secondary"])

    div1 = ft.Divider(height=1, color=initial_pal["border_subtle"])
    div2 = ft.Divider(height=1, color=initial_pal["border_subtle"])
    div3 = ft.Divider(height=1, color=initial_pal["border_subtle"])
    div4 = ft.Divider(height=1, color=initial_pal["border_subtle"])

    # ── Input Fields ───────────────────────────────────────────────
    lat_field = styled_text_field(value=str(selected_lat[0]), label="Latitude", expand=True)
    lon_field = styled_text_field(value=str(selected_lon[0]), label="Longitude", expand=True)

    scale_dropdown = styled_dropdown(
        value="1000",
        options=[ft.dropdown.Option(str(s), f"1:{s:,}") for s in SCALE_OPTIONS],
    )
    paper_dropdown = styled_dropdown(
        value="A3 Landscape",
        options=[ft.dropdown.Option(k) for k in PAPER_SIZES.keys()],
    )
    margin_field = styled_text_field(
        value="15", label="Border (mm)", keyboard_type=ft.KeyboardType.NUMBER, expand=True
    )
    format_dropdown = styled_dropdown(
        value="pdf",
        options=[
            ft.dropdown.Option("pdf", "PDF (Print)"),
            ft.dropdown.Option("svg", "SVG (Vector)"),
            ft.dropdown.Option("dxf", "DXF (CAD)"),
        ],
        expand=True,
    )
    filename_field = styled_text_field(value="schwarzplan_a3_1_1000.pdf", label="Filename")

    # ── Urban Context Layers (Buildings, Water, Greenery, Roadways) ──
    def bind_color_field(field, swatch, on_valid):
        """
        Keeps a swatch in step with a hex field and marks unreadable input.

        Only a colour that parses reaches on_valid, so a half-typed hex can
        never reach an export.
        """
        def handler(e):
            parsed = normalize_hex(field.value)
            if parsed:
                on_valid(parsed)
                swatch.bgcolor = parsed
                field.border_color = current_theme()["border_subtle"]
            else:
                field.border_color = ERROR_RED
            page.update()

        field.on_change = handler

    class LayerControl:
        """A checkbox, colour swatch and hex field for one map layer."""

        def __init__(self, label, hex_label, default_hex, checked):
            self.color = default_hex
            self.checkbox = ft.Checkbox(
                label=label,
                value=checked,
                label_style=ft.TextStyle(
                    size=12, color=initial_pal["text_primary"], weight=ft.FontWeight.W_500
                ),
                active_color=initial_pal["accent"],
                on_change=lambda e: update_coverage_and_preview(),
            )
            self.swatch = ft.Container(
                width=20, height=20, border_radius=4,
                bgcolor=self.color,
                border=ft.Border.all(1, initial_pal["border_subtle"]),
            )
            self.field = styled_text_field(
                value=self.color, label=hex_label, width=100, height=40,
            )
            bind_color_field(self.field, self.swatch, self._set_color)

        def _set_color(self, value):
            self.color = value

        @property
        def enabled(self):
            return bool(self.checkbox.value)

        def apply_theme(self, pal):
            self.checkbox.label_style = ft.TextStyle(
                size=12, color=pal["text_primary"], weight=ft.FontWeight.W_500
            )
            self.checkbox.active_color = pal["accent"]
            self.swatch.border = ft.Border.all(1, pal["border_subtle"])

        def row(self):
            return ft.Row(
                [ft.Container(content=self.checkbox, expand=True), self.swatch, self.field],
                spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

    building_layer = LayerControl("Buildings (Schwarzplan)", "Bldg Hex", "#000000", True)
    water_layer = LayerControl("Waterways (Blauplan)", "Water Hex", "#C5DCE8", False)
    greenery_layer = LayerControl("Parks & Greenery (Gr\u00fcnplan)", "Green Hex", "#DCE8D8", False)
    roadway_layer = LayerControl("Roadways (Strassennetz)", "Road Hex", "#A0A0A0", False)
    map_layers = [building_layer, water_layer, greenery_layer, roadway_layer]

    # Paper colour is a print decision, not a UI-theme decision, so it gets its
    # own control and stays white whichever theme the app is in.
    background_swatch = ft.Container(
        width=20, height=20, border_radius=4,
        bgcolor="#FFFFFF",
        border=ft.Border.all(1, initial_pal["border_subtle"]),
    )
    background_color = ["#FFFFFF"]
    background_field = styled_text_field(
        value="#FFFFFF", label="Paper Hex", width=100, height=40,
    )

    bind_color_field(
        background_field, background_swatch,
        lambda value: background_color.__setitem__(0, value),
    )
    background_label = ft.Text(
        "Paper Background", size=12, color=initial_pal["text_primary"],
        weight=ft.FontWeight.W_500,
    )

    # ── Coverage Badge & Bounding Box Marker ───────────────────────
    coverage_icon = ft.Icon(ft.Icons.SQUARE_FOOT, color=initial_pal["accent"], size=14)
    coverage_text = ft.Text("Coverage: calculating…", size=11, color=initial_pal["accent"], weight=ft.FontWeight.W_500)
    coverage_badge = ft.Container(
        content=ft.Row([
            coverage_icon,
            coverage_text,
        ], spacing=4),
        bgcolor=ft.Colors.with_opacity(0.12, initial_pal["accent"]),
        border_radius=6,
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        margin=ft.Margin.only(top=4, bottom=4),
    )

    bbox_poly_marker = ftm.PolygonMarker(
        coordinates=[],
        border_color=initial_pal["accent"],
        border_stroke_width=2.5,
        color=ft.Colors.with_opacity(0.18, initial_pal["accent"]),
    )

    # Filenames for the layer combinations that have an established German name.
    PLAN_NAMES = {
        (True, False, False, False): "schwarzplan",
        (False, True, False, False): "blauplan",
        (False, False, True, False): "gruenplan",
        (False, False, False, True): "strassenplan",
        (False, True, True, False): "freiraumplan",
    }

    def _set_coverage(text, ok=True):
        pal = current_theme()
        coverage_text.value = text
        coverage_text.color = pal["accent"] if ok else WARNING_AMBER
        coverage_icon.color = pal["accent"] if ok else WARNING_AMBER
        coverage_icon.icon = ft.Icons.SQUARE_FOOT if ok else ft.Icons.WARNING_AMBER_ROUNDED
        coverage_badge.bgcolor = ft.Colors.with_opacity(
            0.12, pal["accent"] if ok else WARNING_AMBER
        )

    def _clear_preview_box():
        """Drop the rectangle so an invalid setting cannot leave a stale one."""
        bbox_poly_marker.coordinates = []
        if polygon_layer_ref.current:
            polygon_layer_ref.current.polygons = [bbox_poly_marker]
            polygon_layer_ref.current.update()

    def update_coverage_and_preview(e=None):
        try:
            try:
                scale_val = int(scale_dropdown.value or "1000")
            except (TypeError, ValueError):
                scale_val = 1000
            paper_key = paper_dropdown.value or "A3 Landscape"
            fmt_ext = format_dropdown.value or "pdf"
            p = PAPER_SIZES.get(paper_key, PAPER_SIZES["A3 Landscape"])

            try:
                margin_mm = float(margin_field.value)
            except (TypeError, ValueError):
                # Say so rather than freezing the badge on a stale number.
                _set_coverage("Border must be a number in millimetres", ok=False)
                _clear_preview_box()
                page.update()
                return

            if margin_mm < 0 or 2 * margin_mm >= min(p["width_mm"], p["height_mm"]):
                _set_coverage("Border does not fit the selected paper", ok=False)
                _clear_preview_box()
                page.update()
                return

            coords, rw, rh = calculate_bbox_corners(
                selected_lat[0], selected_lon[0],
                p["width_mm"], p["height_mm"],
                margin_mm, scale_val,
            )

            # Say up front that this cannot be fetched, rather than after the
            # user has clicked Generate and waited.
            query_radius_m = (max(rw, rh) / 2.0) * 1.15
            if query_radius_m > MAX_QUERY_RADIUS_M:
                _set_coverage(
                    f"{max(rw, rh) / 1000.0:.1f} km across — too large to fetch. "
                    f"Use a smaller sheet or a larger scale number.",
                    ok=False,
                )
            else:
                area_km2 = (rw * rh) / 1_000_000.0
                _set_coverage(f"Print Coverage: {rw:.0f}m × {rh:.0f}m ({area_km2:.2f} km²)")

            if filename_is_custom[0]:
                # Keep the name, but track the format the user selected.
                filename_field.value = with_extension(filename_field.value, fmt_ext)
            else:
                safe_paper = paper_key.lower().replace(" ", "_").replace("×", "x")
                selection = (
                    building_layer.enabled, water_layer.enabled,
                    greenery_layer.enabled, roadway_layer.enabled,
                )
                prefix = PLAN_NAMES.get(selection) or (
                    "schwarzplan_context" if selection[0] else "urban_context"
                )
                filename_field.value = f"{prefix}_{safe_paper}_1_{scale_val}.{fmt_ext}"

            bbox_poly_marker.coordinates = coords
            if polygon_layer_ref.current:
                polygon_layer_ref.current.polygons = [bbox_poly_marker]
                polygon_layer_ref.current.update()
            if map_ref.current:
                map_ref.current.update()

            page.update()
        except Exception:
            # Silently passing here hides real bugs, so leave a trace.
            __log_crash(None)

    scale_dropdown.on_select = update_coverage_and_preview
    scale_dropdown.on_change = update_coverage_and_preview
    paper_dropdown.on_select = update_coverage_and_preview
    paper_dropdown.on_change = update_coverage_and_preview
    format_dropdown.on_select = update_coverage_and_preview
    format_dropdown.on_change = update_coverage_and_preview
    margin_field.on_change = update_coverage_and_preview
    margin_field.on_blur = update_coverage_and_preview
    margin_field.on_submit = update_coverage_and_preview

    def on_filename_edited(e):
        filename_is_custom[0] = bool((filename_field.value or "").strip())

    filename_field.on_change = on_filename_edited

    # ── Moving the map ─────────────────────────────────────────────
    async def go_to(lat, lon, zoom=None):
        """
        Recentres the map and the pin on a coordinate.

        Map.move_to is a coroutine. Calling it without awaiting builds the
        coroutine and drops it, which is why the map used to stay put.
        """
        selected_lat[0] = lat
        selected_lon[0] = lon
        lat_field.value = str(lat)
        lon_field.value = str(lon)

        if marker_layer_ref.current:
            marker_layer_ref.current.markers = [create_marker(lat, lon)]
            marker_layer_ref.current.update()

        update_coverage_and_preview()

        if map_ref.current:
            destination = ftm.MapLatitudeLongitude(lat, lon)
            if zoom is None:
                await map_ref.current.move_to(destination=destination)
            else:
                await map_ref.current.move_to(destination=destination, zoom=zoom)

    # ── Coordinates manual edit handler ────────────────────────────
    async def on_coords_changed(e=None):
        try:
            lat = float(lat_field.value)
            lon = float(lon_field.value)
        except (TypeError, ValueError):
            return
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            await go_to(round(lat, 6), round(lon, 6))

    lat_field.on_submit = on_coords_changed
    lat_field.on_blur = on_coords_changed
    lon_field.on_submit = on_coords_changed
    lon_field.on_blur = on_coords_changed

    # ── Progress / Status & Open Action ─────────────────────────────
    progress_bar = ft.ProgressBar(
        value=0, color=initial_pal["accent"], bgcolor=initial_pal["bg_input"],
        bar_height=4, border_radius=2, visible=False,
    )
    status_text = ft.Text("", size=12, color=initial_pal["text_secondary"], text_align=ft.TextAlign.CENTER)
    status_icon = ft.Icon(ft.Icons.INFO_OUTLINE, color=initial_pal["accent"], size=16, visible=False)

    def show_status(text, ok=None, show_actions=False):
        """Drives the status line, its icon and the two file buttons together."""
        status_text.value = text
        if ok is None:
            status_icon.visible = False
            status_text.color = current_theme()["text_secondary"]
        else:
            status_icon.visible = True
            status_icon.icon = ft.Icons.CHECK_CIRCLE_OUTLINE if ok else ft.Icons.ERROR_OUTLINE
            status_icon.color = SUCCESS_GREEN if ok else ERROR_RED
            status_text.color = SUCCESS_GREEN if ok else ERROR_RED
        open_file_btn.visible = show_actions
        open_folder_btn.visible = show_actions
        try:
            page.update()
        except Exception:
            pass

    def _reveal(path, in_folder):
        """
        Hands the path to the OS as an argument, never as shell text.

        A filename can legitimately contain quotes and semicolons, and string
        interpolation into a shell command would run them.
        """
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path] if in_folder else ["open", path], check=False)
        elif sys.platform == "win32":
            if in_folder:
                subprocess.run(["explorer", f"/select,{path}"], check=False)
            else:
                os.startfile(path)  # noqa: S606 - Windows-only, takes a path not a command
        else:
            target = os.path.dirname(path) if in_folder else path
            subprocess.run(["xdg-open", target], check=False)

    def open_generated_file(e):
        path = last_generated_file[0]
        if path and os.path.exists(path):
            try:
                _reveal(path, in_folder=False)
            except Exception:
                show_status(f"Could not open {os.path.basename(path)}.", ok=False)

    def open_folder(e):
        path = last_generated_file[0]
        if path and os.path.exists(path):
            try:
                _reveal(path, in_folder=True)
            except Exception:
                show_status("Could not open the containing folder.", ok=False)

    open_file_btn = ft.IconButton(
        icon=ft.Icons.OPEN_IN_NEW,
        icon_color=SUCCESS_GREEN,
        icon_size=18,
        tooltip="Open file",
        visible=False,
        on_click=open_generated_file,
    )
    open_folder_btn = ft.IconButton(
        icon=ft.Icons.FOLDER_ROUNDED,
        icon_color=initial_pal["accent"],
        icon_size=18,
        tooltip="Reveal in Finder / Explorer",
        visible=False,
        on_click=open_folder,
    )

    status_row = ft.Row(
        [status_icon, status_text, open_file_btn, open_folder_btn],
        alignment=ft.MainAxisAlignment.CENTER, spacing=4,
    )

    # ── File Picker ────────────────────────────────────────────────
    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    async def browse_clicked(e):
        fmt = format_dropdown.value or "pdf"
        result = await file_picker.save_file(
            dialog_title="Save Schwarzplan Diagram",
            file_name=with_extension(filename_field.value, fmt),
            allowed_extensions=[fmt],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if result:
            chosen_save_dir[0] = os.path.dirname(result) or None
            filename_field.value = with_extension(os.path.basename(result), fmt)
            filename_is_custom[0] = True
            refresh_save_dir_label()
            page.update()

    save_dir_text = ft.Text(
        "", size=10, color=initial_pal["text_secondary"], no_wrap=True,
        tooltip="", overflow=ft.TextOverflow.ELLIPSIS,
    )

    def refresh_save_dir_label():
        folder = chosen_save_dir[0] or default_save_dir()
        home = os.path.expanduser("~")
        shown = "~" + folder[len(home):] if folder.startswith(home) else folder
        save_dir_text.value = f"Saving to {shown}"
        save_dir_text.tooltip = folder

    browse_btn = ft.IconButton(
        icon=ft.Icons.FOLDER_OPEN_ROUNDED,
        icon_color=initial_pal["accent"], icon_size=18,
        tooltip="Browse save location",
        on_click=browse_clicked,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    # ── Generate Button ────────────────────────────────────────────
    generate_btn = ft.Button(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.AUTO_AWESOME, size=18),
                ft.Text("Generate Plan", size=14, weight=ft.FontWeight.W_600),
            ],
            alignment=ft.MainAxisAlignment.CENTER, spacing=8,
        ),
        bgcolor=initial_pal["accent"],
        color="#000000" if is_dark[0] else "#FFFFFF",
        height=50, width=320,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            elevation=0, overlay_color=initial_pal["accent_hover"],
        ),
    )

    # ── Map & Layers ───────────────────────────────────────────────
    def create_marker(lat, lon):
        return ftm.Marker(
            coordinates=ftm.MapLatitudeLongitude(lat, lon),
            content=ft.Icon(ft.Icons.LOCATION_ON, color=ERROR_RED, size=34),
        )

    tile_layer = ftm.TileLayer(
        ref=tile_layer_ref,
        url_template=initial_pal["tile_url"],
        user_agent_package_name="schwarzplan.app.user_agent",
        min_native_zoom=0,
        max_native_zoom=MAP_MAX_ZOOM,
        min_zoom=MAP_MIN_ZOOM,
        max_zoom=MAP_MAX_ZOOM,
    )

    # Street and place names ride on top of the grey canvas as their own
    # transparent service.
    label_layer = ftm.TileLayer(
        ref=label_layer_ref,
        url_template=initial_pal["label_url"],
        user_agent_package_name="schwarzplan.app.user_agent",
        min_native_zoom=0,
        max_native_zoom=MAP_MAX_ZOOM,
        min_zoom=MAP_MIN_ZOOM,
        max_zoom=MAP_MAX_ZOOM,
    )

    polygon_layer = ftm.PolygonLayer(
        ref=polygon_layer_ref,
        polygons=[bbox_poly_marker],
    )

    marker_layer = ftm.MarkerLayer(
        ref=marker_layer_ref,
        markers=[create_marker(selected_lat[0], selected_lon[0])],
    )

    def on_map_tap(e: ftm.MapTapEvent):
        selected_lat[0] = round(e.coordinates.latitude, 6)
        selected_lon[0] = round(e.coordinates.longitude, 6)
        lat_field.value = str(selected_lat[0])
        lon_field.value = str(selected_lon[0])
        if marker_layer_ref.current:
            marker_layer_ref.current.markers = [create_marker(selected_lat[0], selected_lon[0])]
            marker_layer_ref.current.update()
        update_coverage_and_preview()

    the_map = ftm.Map(
        ref=map_ref,
        expand=True,
        initial_center=ftm.MapLatitudeLongitude(selected_lat[0], selected_lon[0]),
        initial_zoom=14,
        min_zoom=MAP_MIN_ZOOM,
        max_zoom=MAP_MAX_ZOOM,
        on_tap=on_map_tap,
        interaction_configuration=ftm.InteractionConfiguration(
            flags=ftm.InteractionFlag.ALL,
        ),
        layers=[
            tile_layer,
            label_layer,
            polygon_layer,
            marker_layer,
        ],
    )

    # ── Geocoding Search ───────────────────────────────────────────
    search_input = styled_text_field(
        hint_text="Search any city, address, or landmark (e.g. Paris, Tokyo, Berlin Mitte, Manhattan)…",
        expand=True,
        height=44,
    )
    search_status = ft.Text("", size=11, color=initial_pal["text_secondary"])

    def set_search_status(text, ok=None):
        search_status.value = text
        if ok is None:
            search_status.color = current_theme()["text_secondary"]
        else:
            search_status.color = SUCCESS_GREEN if ok else ERROR_RED

    def _geocode(query):
        """Blocking Nominatim lookup, run off the UI loop."""
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": query, "format": "json", "limit": 1}
        )
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # Nominatim's usage policy allows at most one request per second and requires
    # a User-Agent that identifies the app. Going over gets the app blocked for
    # everyone using it, so the limit is enforced here rather than trusted to the
    # user's typing speed.
    search_busy = [False]
    last_search_at = [0.0]

    async def do_search(e=None):
        query = (search_input.value or "").strip()
        if not query:
            return

        # Pasted coordinates need no server round-trip.
        pair = parse_coordinates(query)
        if pair:
            set_search_status(f"Moved to {pair[0]}, {pair[1]}", ok=True)
            await go_to(pair[0], pair[1], zoom=16)
            page.update()
            return

        if search_busy[0]:
            return
        search_busy[0] = True
        set_search_status("Searching OpenStreetMap…")
        page.update()

        try:
            wait = 1.0 - (time.monotonic() - last_search_at[0])
            if wait > 0:
                await asyncio.sleep(wait)
            last_search_at[0] = time.monotonic()
            data = await asyncio.to_thread(_geocode, query)
        except Exception:
            set_search_status("Could not reach the search service.", ok=False)
            page.update()
            return
        finally:
            search_busy[0] = False

        if not data:
            set_search_status("No location found.", ok=False)
            page.update()
            return

        hit = data[0]
        try:
            lat = round(float(hit["lat"]), 6)
            lon = round(float(hit["lon"]), 6)
        except (KeyError, TypeError, ValueError):
            set_search_status("That result had no usable coordinates.", ok=False)
            page.update()
            return

        name = (hit.get("display_name") or query).split(",")[0]
        set_search_status(f"\U0001F4CD {name}", ok=True)
        await go_to(lat, lon, zoom=zoom_for_bounds(hit.get("boundingbox")))
        page.update()

    def on_search_typed(e):
        """Clear a stale result or error as soon as the query changes."""
        if search_status.value:
            set_search_status("")
            page.update()

    search_input.on_change = on_search_typed
    search_input.on_submit = do_search
    search_btn = ft.IconButton(
        icon=ft.Icons.SEARCH_ROUNDED,
        icon_color=initial_pal["accent"],
        icon_size=20,
        tooltip="Search location",
        on_click=do_search,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8), bgcolor=initial_pal["bg_input"]),
    )

    # ── Theme Switcher ─────────────────────────────────────────────
    theme_btn = ft.IconButton(
        icon=ft.Icons.LIGHT_MODE_OUTLINED if is_dark[0] else ft.Icons.DARK_MODE_OUTLINED,
        icon_color=initial_pal["accent"],
        icon_size=20,
        tooltip="Switch to Light Mode" if is_dark[0] else "Switch to Dark Mode",
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8), bgcolor=initial_pal["bg_input"]),
    )

    all_styled_fields = [
        lat_field, lon_field, margin_field, filename_field, search_input,
        background_field,
    ] + [layer.field for layer in map_layers]
    all_styled_dropdowns = [scale_dropdown, paper_dropdown, format_dropdown]

    def toggle_theme(e):
        is_dark[0] = not is_dark[0]
        pal = current_theme()

        # Update page and theme mode
        page.theme_mode = ft.ThemeMode.DARK if is_dark[0] else ft.ThemeMode.LIGHT
        page.bgcolor = pal["bg_dark"]

        # Update Map basemap tiles
        if tile_layer_ref.current:
            tile_layer_ref.current.url_template = pal["tile_url"]
            tile_layer_ref.current.update()
        if label_layer_ref.current:
            label_layer_ref.current.url_template = pal["label_url"]
            label_layer_ref.current.update()
        if map_ref.current:
            map_ref.current.update()

        # Update Theme toggle button
        theme_btn.icon = ft.Icons.LIGHT_MODE_OUTLINED if is_dark[0] else ft.Icons.DARK_MODE_OUTLINED
        theme_btn.icon_color = pal["accent"]
        theme_btn.tooltip = "Switch to Light Mode" if is_dark[0] else "Switch to Dark Mode"
        theme_btn.style = ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8), bgcolor=pal["bg_input"])

        # Update Sidebar colors
        sidebar.bgcolor = pal["bg_panel"]
        sidebar.border = ft.Border.only(right=ft.BorderSide(1, pal["border_subtle"]))
        map_box.border = ft.Border.all(1, pal["border_subtle"])

        # Update Text & Icons
        title_text.color = pal["text_primary"]
        subtitle_text.color = pal["text_secondary"]
        app_icon.color = pal["accent"]
        coords_header.color = pal["text_primary"]
        scale_header.color = pal["text_primary"]
        context_header.color = pal["text_primary"]
        output_header.color = pal["text_primary"]
        scale_label.color = pal["text_secondary"]
        paper_label.color = pal["text_secondary"]
        bottom_helper_text.color = pal["text_secondary"]
        bottom_helper_icon.color = pal["text_secondary"]
        save_dir_text.color = pal["text_secondary"]
        attribution_text.color = pal["text_secondary"]
        attribution_text.value = f"{OSM_ATTRIBUTION}  \u00b7  {pal['tile_attribution']}"

        div1.color = pal["border_subtle"]
        div2.color = pal["border_subtle"]
        div3.color = pal["border_subtle"]
        div4.color = pal["border_subtle"]

        # Update layer controls
        for layer in map_layers:
            layer.apply_theme(pal)
        background_label.color = pal["text_primary"]
        background_swatch.border = ft.Border.all(1, pal["border_subtle"])

        # Update Form Fields & Dropdowns
        for f in all_styled_fields:
            f.bgcolor = pal["bg_input"]
            f.color = pal["text_primary"]
            f.border_color = pal["border_subtle"]
            f.focused_border_color = pal["accent"]
            f.cursor_color = pal["accent"]
            f.label_style = ft.TextStyle(size=11, color=pal["text_secondary"])

        # The loop above repaints every border, so restore the warning on any
        # colour field that still holds text we cannot read.
        for f in [background_field] + [layer.field for layer in map_layers]:
            if normalize_hex(f.value) is None:
                f.border_color = ERROR_RED

        for d in all_styled_dropdowns:
            d.bgcolor = pal["bg_input"]
            d.color = pal["text_primary"]
            d.border_color = pal["border_subtle"]
            d.focused_border_color = pal["accent"]

        # Update Search button & Browse button
        search_btn.icon_color = pal["accent"]
        search_btn.style = ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8), bgcolor=pal["bg_input"])
        browse_btn.icon_color = pal["accent"]

        # Update Coverage Badge & Bounding Box Marker
        coverage_icon.color = pal["accent"]
        coverage_text.color = pal["accent"]
        coverage_badge.bgcolor = ft.Colors.with_opacity(0.12, pal["accent"])
        bbox_poly_marker.border_color = pal["accent"]
        bbox_poly_marker.color = ft.Colors.with_opacity(0.18, pal["accent"])

        # Update Generate button & progress
        generate_btn.bgcolor = pal["accent"]
        generate_btn.color = "#000000" if is_dark[0] else "#FFFFFF"
        generate_btn.style = ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            elevation=0, overlay_color=pal["accent_hover"],
        )
        progress_bar.color = pal["accent"]
        progress_bar.bgcolor = pal["bg_input"]

        # Refresh map polygon
        if polygon_layer_ref.current:
            polygon_layer_ref.current.polygons = [bbox_poly_marker]
            polygon_layer_ref.current.update()

        page.update()

    theme_btn.on_click = toggle_theme

    # ── Generate Logic ─────────────────────────────────────────────
    def do_generate(e):
        try:
            lat = float(lat_field.value)
            lon = float(lon_field.value)
        except (ValueError, TypeError):
            show_status("Latitude and longitude must be numbers.", ok=False)
            return

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            show_status("Latitude must be -90 to 90 and longitude -180 to 180.", ok=False)
            return

        inc_b = building_layer.enabled
        inc_w = water_layer.enabled
        inc_g = greenery_layer.enabled
        inc_r = roadway_layer.enabled

        if not (inc_b or inc_w or inc_g or inc_r):
            show_status("Select at least one layer to export.", ok=False)
            return

        try:
            margin_mm = float(margin_field.value)
        except (ValueError, TypeError):
            show_status("Border must be a number in millimetres.", ok=False)
            return

        try:
            scale_val = int(scale_dropdown.value)
        except (ValueError, TypeError):
            scale_val = 1000
        paper_val = paper_dropdown.value or "A3 Landscape"
        fmt_val = format_dropdown.value or "pdf"

        # The format dropdown decides the format, so the name always matches it.
        fname = with_extension(filename_field.value, fmt_val)
        filename_field.value = fname

        output_path = os.path.join(chosen_save_dir[0] or default_save_dir(), fname)
        last_generated_file[0] = output_path

        generate_btn.disabled = True
        progress_bar.visible = True
        progress_bar.value = 0
        show_status("Contacting OpenStreetMap…")

        def on_progress(text, pct):
            progress_bar.value = pct if pct >= 0 else None
            status_text.value = text
            try:
                page.update()
            except Exception:
                pass

        def run_generation():
            try:
                result = generate_schwarzplan(
                    center_lat=lat, center_lon=lon,
                    scale=scale_val, paper_size=paper_val,
                    margin_mm=margin_mm, output_path=output_path,
                    include_buildings=inc_b,
                    building_hex=building_layer.color,
                    include_water=inc_w,
                    water_hex=water_layer.color,
                    include_greenery=inc_g,
                    greenery_hex=greenery_layer.color,
                    include_roads=inc_r,
                    road_hex=roadway_layer.color,
                    background_hex=background_color[0],
                    on_progress=on_progress,
                )
            except Exception:
                # Without this the worker thread would die silently and leave the
                # button disabled and the progress bar spinning forever.
                __log_crash(None)
                progress_bar.visible = False
                show_status("Something went wrong. See the log in the Schwarzplan folder.", ok=False)
                return
            finally:
                generate_btn.disabled = False

            if result["success"]:
                progress_bar.value = 1.0
                counts = [
                    (inc_b, "building_count", "bldgs"),
                    (inc_w, "water_count", "water"),
                    (inc_g, "greenery_count", "parks"),
                    (inc_r, "road_count", "roads"),
                ]
                parts = [
                    f"{result.get(key, 0)} {word}"
                    for included, key, word in counts if included
                ]
                show_status(
                    f"{os.path.basename(result['output_path'])} ({', '.join(parts)})",
                    ok=True, show_actions=True,
                )
            else:
                progress_bar.visible = False
                show_status(result["message"], ok=False)

        threading.Thread(target=run_generation, daemon=True).start()

    generate_btn.on_click = do_generate

    # ── Sidebar ────────────────────────────────────────────────────
    sidebar = ft.Container(
        content=ft.Column(
            [
                # Title + Theme Toggle
                ft.Container(
                    content=ft.Row([
                        ft.Row([
                            app_icon,
                            ft.Column([
                                title_text,
                                subtitle_text,
                            ], spacing=1),
                        ], spacing=8),
                        ft.Container(expand=True),
                        theme_btn,
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.only(bottom=12),
                ),
                div1,

                # Location
                ft.Container(height=4),
                coords_header,
                ft.Row([lat_field, lon_field], spacing=8),

                ft.Container(height=6),
                div2,

                # Scale & Paper Settings
                ft.Container(height=4),
                scale_header,
                scale_label,
                scale_dropdown,
                ft.Container(height=4),
                paper_label,
                paper_dropdown,
                ft.Container(height=4),
                ft.Row([margin_field, format_dropdown], spacing=8),
                coverage_badge,

                ft.Container(height=6),
                div3,

                # Urban Context Layers (Buildings, Water, Greenery & Roadways)
                ft.Container(height=4),
                context_header,
                *[layer.row() for layer in map_layers],
                ft.Row([
                    ft.Container(content=background_label, expand=True),
                    background_swatch,
                    background_field,
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),

                ft.Container(height=6),
                div4,

                # Output
                ft.Container(height=4),
                output_header,
                ft.Row(
                    [ft.Container(content=filename_field, expand=True), browse_btn],
                    spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(content=save_dir_text, padding=ft.Padding.only(left=2, top=2)),

                # Spacer
                ft.Container(expand=True),

                # Generate
                ft.Container(
                    content=ft.Column([
                        progress_bar,
                        ft.Container(content=generate_btn, alignment=ft.Alignment.CENTER),
                        status_row,
                    ], spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.only(top=6),
                ),
            ],
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
        ),
        width=360,
        bgcolor=initial_pal["bg_panel"],
        border_radius=ft.BorderRadius.only(top_right=16, bottom_right=16),
        padding=ft.Padding.all(18),
        border=ft.Border.only(right=ft.BorderSide(1, initial_pal["border_subtle"])),
    )

    # ── Map Container ──────────────────────────────────────────────
    map_box = ft.Container(
        content=the_map,
        expand=True,
        border_radius=12,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        border=ft.Border.all(1, initial_pal["border_subtle"]),
    )

    bottom_helper_icon = ft.Icon(ft.Icons.TOUCH_APP, size=13, color=initial_pal["text_secondary"])
    bottom_helper_text = ft.Text("Click on the map to set center pin. Drag or pinch/scroll on trackpad to zoom & navigate.", size=11, color=initial_pal["text_secondary"])
    attribution_text = ft.Text(
        f"{OSM_ATTRIBUTION}  \u00b7  {initial_pal['tile_attribution']}",
        size=10, color=initial_pal["text_secondary"],
    )

    map_view = ft.Container(
        content=ft.Column([
            # Clean Search Bar & Status
            ft.Container(
                content=ft.Row([
                    search_input,
                    search_btn,
                    search_status,
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.only(bottom=8),
            ),
            # Interactive Map with Live Bounding Box & Touchpad Zoom
            map_box,
            # Bottom Info Helper
            ft.Container(
                content=ft.Row([
                    bottom_helper_icon,
                    bottom_helper_text,
                    ft.Container(expand=True),
                    attribution_text,
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.only(top=6, left=4),
            ),
        ], spacing=0),
        expand=True,
        padding=ft.Padding.only(top=14, right=16, bottom=14, left=12),
    )

    # ── Main Layout ────────────────────────────────────────────────
    page.add(
        ft.Row(
            [sidebar, map_view],
            expand=True,
            spacing=0,
        )
    )

    # Initialize coverage calculation & bounding box preview
    refresh_save_dir_label()
    update_coverage_and_preview()


if __name__ == "__main__":
    try:
        ft.run(main)
    except Exception as e:
        __log_crash(e)
        raise





