"""
Schwarzplan Generator — Desktop Application

A modern GUI for generating architectural figure-ground diagrams
from OpenStreetMap building data at exact architectural scales.
"""

import os
import sys
import tempfile
import traceback
import threading

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


# ── Color Palette ──────────────────────────────────────────────────
BG_DARK = "#0D0D0D"
BG_PANEL = "#161616"
BG_CARD = "#1E1E1E"
BG_INPUT = "#252525"
ACCENT = "#4FC3F7"
ACCENT_HOVER = "#81D4FA"
TEXT_PRIMARY = "#F0F0F0"
TEXT_SECONDARY = "#8A8A8A"
BORDER_SUBTLE = "#2A2A2A"
SUCCESS_GREEN = "#66BB6A"
ERROR_RED = "#EF5350"


def main(page: ft.Page):
    # ── Page Setup ─────────────────────────────────────────────────
    page.title = "Schwarzplan Generator"
    page.bgcolor = BG_DARK
    page.padding = 0
    page.window.min_width = 980
    page.window.min_height = 660
    page.window.width = 1240
    page.window.height = 800
    page.fonts = {
        "Inter": "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap",
    }
    page.theme = ft.Theme(
        font_family="Inter",
        color_scheme=ft.ColorScheme(
            primary=ACCENT,
            on_primary="#000000",
            surface=BG_CARD,
            on_surface=TEXT_PRIMARY,
        ),
    )
    page.theme_mode = ft.ThemeMode.DARK

    # ── State ──────────────────────────────────────────────────────
    selected_lat = 53.5581
    selected_lon = 9.9632
    chosen_save_path = [None]
    marker_layer_ref = ft.Ref[ftm.MarkerLayer]()

    # ── Helpers ────────────────────────────────────────────────────
    def label(text):
        return ft.Text(text, size=11, weight=ft.FontWeight.W_500, color=TEXT_SECONDARY)

    def section_header(text):
        return ft.Container(
            content=ft.Text(text, size=13, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
            margin=ft.Margin.only(bottom=6, top=4),
        )

    _pad_field = ft.Padding.symmetric(horizontal=12, vertical=4)

    def styled_text_field(**kwargs):
        defaults = dict(
            text_size=13,
            label_style=ft.TextStyle(size=11, color=TEXT_SECONDARY),
            bgcolor=BG_INPUT,
            border_color=BORDER_SUBTLE,
            focused_border_color=ACCENT,
            color=TEXT_PRIMARY,
            cursor_color=ACCENT,
            border_radius=8,
            height=46,
            content_padding=_pad_field,
        )
        defaults.update(kwargs)
        return ft.TextField(**defaults)

    def styled_dropdown(**kwargs):
        defaults = dict(
            text_size=13,
            bgcolor=BG_INPUT,
            border_color=BORDER_SUBTLE,
            focused_border_color=ACCENT,
            color=TEXT_PRIMARY,
            border_radius=8,
            height=46,
            content_padding=_pad_field,
        )
        defaults.update(kwargs)
        return ft.Dropdown(**defaults)

    # ── Input Fields ───────────────────────────────────────────────
    lat_field = styled_text_field(value=str(selected_lat), label="Latitude")
    lon_field = styled_text_field(value=str(selected_lon), label="Longitude")

    scale_dropdown = styled_dropdown(
        value="1000",
        options=[ft.dropdown.Option(str(s), f"1:{s:,}") for s in SCALE_OPTIONS],
    )
    paper_dropdown = styled_dropdown(
        value="A3 Landscape",
        options=[ft.dropdown.Option(k) for k in PAPER_SIZES.keys()],
    )
    margin_field = styled_text_field(
        value="15", label="Border Margin (mm)", keyboard_type=ft.KeyboardType.NUMBER,
    )
    format_dropdown = styled_dropdown(
        value="pdf",
        options=[
            ft.dropdown.Option("pdf", "PDF (Vector Print)"),
            ft.dropdown.Option("svg", "SVG (Illustrator/Vector)"),
            ft.dropdown.Option("dxf", "DXF (CAD / AutoCAD)"),
        ],
    )
    filename_field = styled_text_field(value="schwarzplan_a3_1_1000.pdf", label="Filename")

    # ── Coverage Badge ─────────────────────────────────────────────
    coverage_text = ft.Text("Coverage: calculating…", size=11, color=ACCENT, weight=ft.FontWeight.W_500)
    coverage_badge = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.SQUARE_FOOT, color=ACCENT, size=14),
            coverage_text,
        ], spacing=4),
        bgcolor=ft.Colors.with_opacity(0.12, ACCENT),
        border_radius=6,
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        margin=ft.Margin.only(top=4, bottom=4),
    )

    def update_coverage_and_filename(e=None):
        try:
            scale_val = int(scale_dropdown.value)
            paper_key = paper_dropdown.value
            margin_mm = float(margin_field.value or "15")
            fmt_ext = format_dropdown.value or "pdf"

            if paper_key in PAPER_SIZES:
                p = PAPER_SIZES[paper_key]
                mw = (p["width_mm"] - 2 * margin_mm) / 1000.0 * scale_val
                mh = (p["height_mm"] - 2 * margin_mm) / 1000.0 * scale_val
                area_km2 = (mw * mh) / 1_000_000.0

                if mw > 0 and mh > 0:
                    coverage_text.value = f"Coverage: {mw:.0f}m × {mh:.0f}m ({area_km2:.2f} km²)"
                else:
                    coverage_text.value = "Margin exceeds paper size"

                # Update default filename if user hasn't set custom path
                if chosen_save_path[0] is None:
                    safe_paper = paper_key.lower().replace(" ", "_").replace("×", "x")
                    filename_field.value = f"schwarzplan_{safe_paper}_1_{scale_val}.{fmt_ext}"
            page.update()
        except Exception:
            pass

    scale_dropdown.on_change = update_coverage_and_filename
    paper_dropdown.on_change = update_coverage_and_filename
    margin_field.on_change = update_coverage_and_filename
    format_dropdown.on_change = update_coverage_and_filename

    # ── Progress / Status ──────────────────────────────────────────
    progress_bar = ft.ProgressBar(
        value=0, color=ACCENT, bgcolor=BG_INPUT,
        bar_height=4, border_radius=2, visible=False,
    )
    status_text = ft.Text("", size=12, color=TEXT_SECONDARY, text_align=ft.TextAlign.CENTER)
    status_icon = ft.Icon(ft.Icons.INFO_OUTLINE, color=ACCENT, size=16, visible=False)
    status_row = ft.Row(
        [status_icon, status_text],
        alignment=ft.MainAxisAlignment.CENTER, spacing=6,
    )

    # ── File Picker ────────────────────────────────────────────────
    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)

    def browse_clicked(e):
        fmt = format_dropdown.value or "pdf"
        default_name = filename_field.value or f"schwarzplan.{fmt}"
        result = file_picker.save_file(
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
        icon_color=ACCENT, icon_size=18,
        tooltip="Browse save location",
        on_click=browse_clicked,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    # ── Generate Button ────────────────────────────────────────────
    generate_btn = ft.Button(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.AUTO_AWESOME, size=18),
                ft.Text("Generate Schwarzplan", size=14, weight=ft.FontWeight.W_600),
            ],
            alignment=ft.MainAxisAlignment.CENTER, spacing=8,
        ),
        bgcolor=ACCENT, color="#000000", height=50, width=300,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            elevation=0, overlay_color=ACCENT_HOVER,
        ),
    )

    # ── Map ────────────────────────────────────────────────────────
    def create_marker(lat, lon):
        return ftm.Marker(
            coordinates=ftm.MapLatitudeLongitude(lat, lon),
            content=ft.Icon(ft.Icons.LOCATION_ON, color=ERROR_RED, size=32),
        )

    marker_layer = ftm.MarkerLayer(
        ref=marker_layer_ref,
        markers=[create_marker(selected_lat, selected_lon)],
    )

    def on_map_tap(e: ftm.MapTapEvent):
        nonlocal selected_lat, selected_lon
        selected_lat = round(e.coordinates.latitude, 6)
        selected_lon = round(e.coordinates.longitude, 6)
        lat_field.value = str(selected_lat)
        lon_field.value = str(selected_lon)
        if marker_layer_ref.current:
            marker_layer_ref.current.markers = [create_marker(selected_lat, selected_lon)]
            marker_layer_ref.current.update()
        page.update()

    the_map = ftm.Map(
        expand=True,
        initial_center=ftm.MapLatitudeLongitude(selected_lat, selected_lon),
        initial_zoom=14, min_zoom=3, max_zoom=19,
        on_tap=on_map_tap,
        interaction_configuration=ftm.InteractionConfiguration(
            flags=ftm.InteractionFlag.ALL,
        ),
        layers=[
            ftm.TileLayer(
                url_template="https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
                user_agent_package_name="schwarzplan.app.user_agent",
            ),
            marker_layer,
        ],
    )

    map_container = ft.Container(
        content=ft.Stack([
            the_map,
            ft.Container(
                content=ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.TOUCH_APP, size=14, color=TEXT_SECONDARY),
                            ft.Text("Click anywhere on the map to set the center point",
                                    size=11, color=TEXT_SECONDARY),
                        ],
                        spacing=6,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.85, BG_DARK),
                    border_radius=8,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                ),
                alignment=ft.Alignment.BOTTOM_CENTER,
                margin=ft.Margin.only(bottom=12),
            ),
        ]),
        expand=True,
        border_radius=12,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        border=ft.Border.all(1, BORDER_SUBTLE),
    )

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
            page.update()
            return

        try:
            margin_mm = float(margin_field.value)
        except (ValueError, TypeError):
            margin_mm = 15.0

        scale_val = int(scale_dropdown.value)
        paper_val = paper_dropdown.value
        fmt_val = format_dropdown.value or "pdf"

        fname = filename_field.value or f"schwarzplan.{fmt_val}"
        if not any(fname.lower().endswith(ext) for ext in SUPPORTED_FORMATS):
            fname = f"{fname}.{fmt_val}"

        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.exists(desktop_path) or not os.access(desktop_path, os.W_OK):
            desktop_path = os.path.expanduser("~")

        output_path = chosen_save_path[0] or os.path.join(desktop_path, fname)

        generate_btn.disabled = True
        progress_bar.visible = True
        progress_bar.value = 0
        status_icon.visible = False
        status_text.value = "Contacting OpenStreetMap…"
        status_text.color = TEXT_SECONDARY
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
                on_progress=on_progress,
            )

            generate_btn.disabled = False
            if result["success"]:
                progress_bar.value = 1.0
                status_icon.visible = True
                status_icon.icon = ft.Icons.CHECK_CIRCLE_OUTLINE
                status_icon.color = SUCCESS_GREEN
                count_info = f" ({result.get('building_count', 0)} buildings)"
                status_text.value = f"✓ Saved {os.path.basename(result['output_path'])}{count_info}"
                status_text.color = SUCCESS_GREEN
            else:
                progress_bar.visible = False
                status_icon.visible = True
                status_icon.icon = ft.Icons.ERROR_OUTLINE
                status_icon.color = ERROR_RED
                status_text.value = result["message"]
                status_text.color = ERROR_RED
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
                # Title
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Icon(ft.Icons.GRID_ON_ROUNDED, color=ACCENT, size=22),
                            ft.Text("Schwarzplan", size=20, weight=ft.FontWeight.W_700, color=TEXT_PRIMARY),
                        ], spacing=8),
                        ft.Text("Architectural Figure-Ground Generator", size=11, color=TEXT_SECONDARY),
                    ], spacing=2),
                    padding=ft.Padding.only(bottom=14),
                ),
                ft.Divider(height=1, color=BORDER_SUBTLE),

                # Location
                ft.Container(height=6),
                section_header("📍  Center Coordinates"),
                ft.Row([lat_field, lon_field], spacing=8),

                ft.Container(height=10),
                ft.Divider(height=1, color=BORDER_SUBTLE),

                # Settings
                ft.Container(height=6),
                section_header("⚙️  Scale & Format"),
                label("Architectural Scale"),
                scale_dropdown,
                ft.Container(height=6),
                label("Paper Format & Orientation"),
                paper_dropdown,
                ft.Container(height=6),
                ft.Row([
                    ft.Container(content=margin_field, expand=True),
                    ft.Container(content=format_dropdown, expand=True),
                ], spacing=8),
                coverage_badge,

                ft.Container(height=10),
                ft.Divider(height=1, color=BORDER_SUBTLE),

                # Output
                ft.Container(height=6),
                section_header("📄  Save Output"),
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
                    ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.only(top=8),
                ),
            ],
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
        ),
        width=330,
        bgcolor=BG_PANEL,
        border_radius=ft.BorderRadius.only(top_right=16, bottom_right=16),
        padding=ft.Padding.all(18),
        border=ft.Border.only(right=ft.BorderSide(1, BORDER_SUBTLE)),
    )

    # ── Main Layout ────────────────────────────────────────────────
    page.add(
        ft.Row(
            [
                sidebar,
                ft.Container(
                    content=ft.Column([
                        ft.Container(
                            content=ft.Text(
                                "Click a location on the map, adjust scale or paper format, then generate.",
                                size=12, color=TEXT_SECONDARY, italic=True,
                            ),
                            padding=ft.Padding.only(left=4, bottom=4),
                        ),
                        map_container,
                    ], spacing=0),
                    expand=True,
                    padding=ft.Padding.only(top=14, right=16, bottom=14, left=12),
                ),
            ],
            expand=True, spacing=0,
        ),
    )

    # Initialize coverage calculation
    update_coverage_and_filename()


if __name__ == "__main__":
    try:
        ft.run(main)
    except Exception as e:
        __log_crash(e)
        raise

