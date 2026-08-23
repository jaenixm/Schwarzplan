"""
Schwarzplan Generator — Desktop Application

A modern GUI for generating architectural figure-ground diagrams
from OpenStreetMap building data at exact architectural scales.
"""

import os
import sys
import ssl
import json
import math
import tempfile
import traceback
import threading
import urllib.request
import urllib.parse

def __log_crash(e):
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.exists(desktop) or not os.access(desktop, os.W_OK):
            desktop = tempfile.gettempdir()
        log_path = os.path.join(desktop, "schwarzplan_crash.txt")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("A fatal error occurred during Schwarzplan App launch:\n\n" + traceback.format_exc())
    except Exception:
        pass

try:
    import flet as ft
    import flet_map as ftm

    from schwarzplan_engine import (
        generate_schwarzplan,
        PAPER_SIZES,
        SCALE_OPTIONS,
        SUPPORTED_FORMATS,
    )
except Exception as e:
    __log_crash(e)
    raise


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
    "tile_url": "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
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
    "tile_url": "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
}

SUCCESS_GREEN = "#66BB6A"
ERROR_RED = "#EF5350"


def calculate_bbox_corners(center_lat: float, center_lon: float, paper_w_mm: float, paper_h_mm: float, margin_mm: float, scale: int):
    """
    Computes geographic coordinates (NW, NE, SE, SW) of the exact printed map area.
    """
    map_w_mm = max(10.0, paper_w_mm - 2 * margin_mm)
    map_h_mm = max(10.0, paper_h_mm - 2 * margin_mm)
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
    chosen_save_path = [None]
    last_generated_file = [None]

    def current_theme():
        return DARK_PALETTE if is_dark[0] else LIGHT_PALETTE

    initial_pal = current_theme()

    # ── Page Setup ─────────────────────────────────────────────────
    page.title = "Schwarzplan Generator"
    page.bgcolor = initial_pal["bg_dark"]
    page.padding = 0
    page.window.min_width = 1020
    page.window.min_height = 680
    page.window.width = 1280
    page.window.height = 840
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

    # ── Urban Context Layers (Buildings, Water, Greenery) ──────────
    building_color_val = ["#000000" if not is_dark[0] else "#F0F0F0"]
    water_color_val = ["#C5DCE8" if not is_dark[0] else "#1E2D3D"]
    greenery_color_val = ["#DCE8D8" if not is_dark[0] else "#203324"]

    building_checkbox = ft.Checkbox(
        label="Buildings (Schwarzplan)",
        value=True,
        label_style=ft.TextStyle(size=12, color=initial_pal["text_primary"], weight=ft.FontWeight.W_500),
        active_color=initial_pal["accent"],
    )
    building_color_box = ft.Container(
        width=20, height=20, border_radius=4,
        bgcolor=building_color_val[0],
        border=ft.Border.all(1, initial_pal["border_subtle"]),
    )
    building_color_field = styled_text_field(
        value=building_color_val[0],
        label="Bldg Hex",
        width=100,
        height=40,
    )

    def on_building_color_change(e):
        hex_val = building_color_field.value.strip()
        if len(hex_val) == 7 and hex_val.startswith("#"):
            building_color_val[0] = hex_val
            building_color_box.bgcolor = hex_val
            page.update()

    building_color_field.on_change = on_building_color_change

    water_checkbox = ft.Checkbox(
        label="Waterways (Blauplan)",
        value=False,
        label_style=ft.TextStyle(size=12, color=initial_pal["text_primary"], weight=ft.FontWeight.W_500),
        active_color=initial_pal["accent"],
    )
    water_color_box = ft.Container(
        width=20, height=20, border_radius=4,
        bgcolor=water_color_val[0],
        border=ft.Border.all(1, initial_pal["border_subtle"]),
    )
    water_color_field = styled_text_field(
        value=water_color_val[0],
        label="Water Hex",
        width=100,
        height=40,
    )

    def on_water_color_change(e):
        hex_val = water_color_field.value.strip()
        if len(hex_val) == 7 and hex_val.startswith("#"):
            water_color_val[0] = hex_val
            water_color_box.bgcolor = hex_val
            page.update()

    water_color_field.on_change = on_water_color_change

    greenery_checkbox = ft.Checkbox(
        label="Parks & Greenery (Grünplan)",
        value=False,
        label_style=ft.TextStyle(size=12, color=initial_pal["text_primary"], weight=ft.FontWeight.W_500),
        active_color=initial_pal["accent"],
    )
    greenery_color_box = ft.Container(
        width=20, height=20, border_radius=4,
        bgcolor=greenery_color_val[0],
        border=ft.Border.all(1, initial_pal["border_subtle"]),
    )
    greenery_color_field = styled_text_field(
        value=greenery_color_val[0],
        label="Green Hex",
        width=100,
        height=40,
    )

    def on_greenery_color_change(e):
        hex_val = greenery_color_field.value.strip()
        if len(hex_val) == 7 and hex_val.startswith("#"):
            greenery_color_val[0] = hex_val
            greenery_color_box.bgcolor = hex_val
            page.update()

    greenery_color_field.on_change = on_greenery_color_change

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

    def update_coverage_and_preview(e=None):
        try:
            scale_val = int(scale_dropdown.value or "1000")
            paper_key = paper_dropdown.value or "A3 Landscape"
            margin_mm = float(margin_field.value or "15")
            fmt_ext = format_dropdown.value or "pdf"
            p = PAPER_SIZES.get(paper_key, PAPER_SIZES["A3 Landscape"])

            coords, rw, rh = calculate_bbox_corners(
                selected_lat[0], selected_lon[0],
                p["width_mm"], p["height_mm"],
                margin_mm, scale_val,
            )

            if rw > 0 and rh > 0:
                area_km2 = (rw * rh) / 1_000_000.0
                coverage_text.value = f"Print Coverage: {rw:.0f}m × {rh:.0f}m ({area_km2:.2f} km²)"
            else:
                coverage_text.value = "Margin exceeds paper size"

            if chosen_save_path[0] is None:
                safe_paper = paper_key.lower().replace(" ", "_").replace("×", "x")
                b = bool(building_checkbox.value)
                w = bool(water_checkbox.value)
                g = bool(greenery_checkbox.value)
                if b and not w and not g:
                    prefix = "schwarzplan"
                elif not b and w and not g:
                    prefix = "blauplan"
                elif not b and not w and g:
                    prefix = "gruenplan"
                elif not b and w and g:
                    prefix = "freiraumplan"
                elif b and (w or g):
                    prefix = "schwarzplan_context"
                else:
                    prefix = "plan"
                filename_field.value = f"{prefix}_{safe_paper}_1_{scale_val}.{fmt_ext}"

            bbox_poly_marker.coordinates = coords
            if polygon_layer_ref.current:
                polygon_layer_ref.current.polygons = [bbox_poly_marker]
                try:
                    polygon_layer_ref.current.update()
                except Exception:
                    pass

            if map_ref.current:
                try:
                    map_ref.current.update()
                except Exception:
                    pass

            page.update()
        except Exception:
            pass

    scale_dropdown.on_select = update_coverage_and_preview
    scale_dropdown.on_change = update_coverage_and_preview
    paper_dropdown.on_select = update_coverage_and_preview
    paper_dropdown.on_change = update_coverage_and_preview
    format_dropdown.on_select = update_coverage_and_preview
    format_dropdown.on_change = update_coverage_and_preview
    margin_field.on_change = update_coverage_and_preview
    margin_field.on_blur = update_coverage_and_preview
    building_checkbox.on_change = update_coverage_and_preview
    water_checkbox.on_change = update_coverage_and_preview
    greenery_checkbox.on_change = update_coverage_and_preview

    # ── Coordinates manual edit handler ────────────────────────────
    def on_coords_changed(e=None):
        try:
            lat = float(lat_field.value)
            lon = float(lon_field.value)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                selected_lat[0] = round(lat, 6)
                selected_lon[0] = round(lon, 6)
                if marker_layer_ref.current:
                    marker_layer_ref.current.markers = [create_marker(selected_lat[0], selected_lon[0])]
                    marker_layer_ref.current.update()
                if map_ref.current:
                    map_ref.current.move_to(
                        destination=ftm.MapLatitudeLongitude(selected_lat[0], selected_lon[0])
                    )
                update_coverage_and_preview()
        except Exception:
            pass

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

    def open_generated_file(e):
        path = last_generated_file[0]
        if path and os.path.exists(path):
            if sys.platform == "darwin":
                os.system(f'open "{path}"')
            elif sys.platform == "win32":
                os.startfile(path)
            else:
                os.system(f'xdg-open "{path}"')

    def open_folder(e):
        path = last_generated_file[0]
        if path and os.path.exists(path):
            if sys.platform == "darwin":
                os.system(f'open -R "{path}"')
            elif sys.platform == "win32":
                os.system(f'explorer /select,"{path}"')

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
        default_name = filename_field.value or f"schwarzplan.{fmt}"
        result = await file_picker.save_file(
            dialog_title="Save Schwarzplan Diagram",
            file_name=default_name,
            allowed_extensions=[fmt, "pdf", "svg", "dxf"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if result:
            chosen_save_path[0] = result
            filename_field.value = os.path.basename(result)
            page.update()

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
        initial_zoom=14, min_zoom=3, max_zoom=19,
        on_tap=on_map_tap,
        interaction_configuration=ftm.InteractionConfiguration(
            flags=ftm.InteractionFlag.ALL,
        ),
        layers=[
            tile_layer,
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

    def do_search(e=None):
        query = (search_input.value or "").strip()
        if not query:
            return
        search_status.value = "Searching OpenStreetMap…"
        search_status.color = current_theme()["text_secondary"]
        page.update()

        def _search_thread():
            try:
                url = f"https://nominatim.openstreetmap.org/search?{urllib.parse.urlencode({'q': query, 'format': 'json', 'limit': 1})}"
                req = urllib.request.Request(url, headers={"User-Agent": "SchwarzplanApp/2.0"})
                ctx = ssl.create_default_context()
                with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data:
                        hit = data[0]
                        new_lat = round(float(hit["lat"]), 6)
                        new_lon = round(float(hit["lon"]), 6)
                        display_name = hit.get("display_name", "")
                        short_name = display_name.split(",")[0]

                        selected_lat[0] = new_lat
                        selected_lon[0] = new_lon
                        lat_field.value = str(new_lat)
                        lon_field.value = str(new_lon)

                        if marker_layer_ref.current:
                            marker_layer_ref.current.markers = [create_marker(new_lat, new_lon)]
                            marker_layer_ref.current.update()
                        if map_ref.current:
                            map_ref.current.move_to(
                                destination=ftm.MapLatitudeLongitude(new_lat, new_lon),
                                zoom=14,
                            )
                        search_status.value = f"📍 Found: {short_name}"
                        search_status.color = SUCCESS_GREEN
                        update_coverage_and_preview()
                    else:
                        search_status.value = "No location found."
                        search_status.color = ERROR_RED
            except Exception as err:
                search_status.value = f"Search error: {str(err)}"
                search_status.color = ERROR_RED
            try:
                page.update()
            except Exception:
                pass

        threading.Thread(target=_search_thread, daemon=True).start()

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
        building_color_field, water_color_field, greenery_color_field,
    ]
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

        div1.color = pal["border_subtle"]
        div2.color = pal["border_subtle"]
        div3.color = pal["border_subtle"]
        div4.color = pal["border_subtle"]

        # Update checkboxes
        building_checkbox.label_style = ft.TextStyle(size=12, color=pal["text_primary"], weight=ft.FontWeight.W_500)
        building_checkbox.active_color = pal["accent"]
        water_checkbox.label_style = ft.TextStyle(size=12, color=pal["text_primary"], weight=ft.FontWeight.W_500)
        water_checkbox.active_color = pal["accent"]
        greenery_checkbox.label_style = ft.TextStyle(size=12, color=pal["text_primary"], weight=ft.FontWeight.W_500)
        greenery_checkbox.active_color = pal["accent"]

        # Update Form Fields & Dropdowns
        for f in all_styled_fields:
            f.bgcolor = pal["bg_input"]
            f.color = pal["text_primary"]
            f.border_color = pal["border_subtle"]
            f.focused_border_color = pal["accent"]
            f.cursor_color = pal["accent"]
            f.label_style = ft.TextStyle(size=11, color=pal["text_secondary"])

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
            status_icon.visible = True
            status_icon.icon = ft.Icons.ERROR_OUTLINE
            status_icon.color = ERROR_RED
            status_text.value = "Invalid coordinate numbers."
            status_text.color = ERROR_RED
            open_file_btn.visible = False
            open_folder_btn.visible = False
            page.update()
            return

        inc_b = bool(building_checkbox.value)
        inc_w = bool(water_checkbox.value)
        inc_g = bool(greenery_checkbox.value)

        if not inc_b and not inc_w and not inc_g:
            status_icon.visible = True
            status_icon.icon = ft.Icons.ERROR_OUTLINE
            status_icon.color = ERROR_RED
            status_text.value = "Please select at least one layer to export."
            status_text.color = ERROR_RED
            open_file_btn.visible = False
            open_folder_btn.visible = False
            page.update()
            return

        try:
            margin_mm = float(margin_field.value)
        except (ValueError, TypeError):
            margin_mm = 15.0

        scale_val = int(scale_dropdown.value)
        paper_val = paper_dropdown.value
        fmt_val = format_dropdown.value or "pdf"

        fname = filename_field.value or f"plan.{fmt_val}"
        if not any(fname.lower().endswith(ext) for ext in SUPPORTED_FORMATS):
            fname = f"{fname}.{fmt_val}"

        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.exists(desktop_path) or not os.access(desktop_path, os.W_OK):
            desktop_path = os.path.expanduser("~")

        output_path = chosen_save_path[0] or os.path.join(desktop_path, fname)
        last_generated_file[0] = output_path

        generate_btn.disabled = True
        progress_bar.visible = True
        progress_bar.value = 0
        status_icon.visible = False
        open_file_btn.visible = False
        open_folder_btn.visible = False
        status_text.value = "Contacting OpenStreetMap…"
        status_text.color = current_theme()["text_secondary"]
        page.update()

        def on_progress(text, pct):
            progress_bar.value = pct if pct >= 0 else None
            status_text.value = text
            try:
                page.update()
            except Exception:
                pass

        def run_generation():
            result = generate_schwarzplan(
                center_lat=lat, center_lon=lon,
                scale=scale_val, paper_size=paper_val,
                margin_mm=margin_mm, output_path=output_path,
                include_buildings=inc_b,
                building_hex=building_color_field.value or ("#000000" if not is_dark[0] else "#FFFFFF"),
                include_water=inc_w,
                water_hex=water_color_field.value or "#C5DCE8",
                include_greenery=inc_g,
                greenery_hex=greenery_color_field.value or "#DCE8D8",
                background_hex="#FFFFFF" if not is_dark[0] else "#0D0D0D",
                on_progress=on_progress,
            )

            generate_btn.disabled = False
            if result["success"]:
                progress_bar.value = 1.0
                status_icon.visible = True
                status_icon.icon = ft.Icons.CHECK_CIRCLE_OUTLINE
                status_icon.color = SUCCESS_GREEN
                
                parts = []
                if inc_b:
                    parts.append(f"{result.get('building_count', 0)} bldgs")
                if inc_w:
                    parts.append(f"{result.get('water_count', 0)} water")
                if inc_g:
                    parts.append(f"{result.get('greenery_count', 0)} parks")
                
                status_text.value = f"✓ {os.path.basename(result['output_path'])} ({', '.join(parts)})"
                status_text.color = SUCCESS_GREEN
                open_file_btn.visible = True
                open_folder_btn.visible = True
            else:
                progress_bar.visible = False
                status_icon.visible = True
                status_icon.icon = ft.Icons.ERROR_OUTLINE
                status_icon.color = ERROR_RED
                status_text.value = result["message"]
                status_text.color = ERROR_RED
                open_file_btn.visible = False
                open_folder_btn.visible = False
            try:
                page.update()
            except Exception:
                pass

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

                # Urban Context Layers (Buildings, Water & Greenery)
                ft.Container(height=4),
                context_header,
                ft.Row([
                    ft.Container(content=building_checkbox, expand=True),
                    building_color_box,
                    building_color_field,
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([
                    ft.Container(content=water_checkbox, expand=True),
                    water_color_box,
                    water_color_field,
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([
                    ft.Container(content=greenery_checkbox, expand=True),
                    greenery_color_box,
                    greenery_color_field,
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
                ], spacing=4),
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
    update_coverage_and_preview()


if __name__ == "__main__":
    try:
        ft.run(main)
    except Exception as e:
        __log_crash(e)
        raise





