"""
Generate a key-map sheet showing all view frames for a channel.
The full channel alignment is fitted (with rotation) into the plan viewport.
The profile viewport is left blank.
"""
import os, sys, math, datetime, time
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import ezdxf
from ezdxf import units
from ezdxf.enums import TextEntityAlignment
import geopandas as gpd
from shapely.ops import substring
from shapely.geometry import Polygon as ShapelyPolygon

from config import (
    TMPL, SHP_PATH, OUT_DXF,
    SHP_BUF_BED, SHP_BUF_CH, SHP_LANDUSE,
    BUFFER_TRUE_COLOR, BUFFER_GLOBAL_W, BUFFER_LINEWEIGHT,
    LANDUSE_ACI_COLOR, LANDUSE_LINEWEIGHT,
    VP_PLAN_X0, VP_PLAN_X1, VP_PLAN_Y0, VP_PLAN_Y1,
    VP_PROF_Y0,
    VP_PLAN_W,  VP_PLAN_H,
    SEGMENT_LEN, PLAN_PERP_M,
)

# Key-map uses the FULL content area (plan + profile zones combined)
KM_VP_X0 = VP_PLAN_X0
KM_VP_X1 = VP_PLAN_X1
KM_VP_Y0 = VP_PROF_Y0    # bottom of profile area
KM_VP_Y1 = VP_PLAN_Y1    # top of plan area
KM_VP_W  = KM_VP_X1 - KM_VP_X0
KM_VP_H  = KM_VP_Y1 - KM_VP_Y0
KM_VP_CX = (KM_VP_X0 + KM_VP_X1) / 2
KM_VP_CY = (KM_VP_Y0 + KM_VP_Y1) / 2
from view_frames import (
    ViewFrame, compute_view_frames,
    warp_satellite_to_frame, _frame_corners,
)
from make_sheet import _geom_rings, _round_scale

from PIL import Image as PILImage

os.makedirs(OUT_DXF, exist_ok=True)

TEXT_STYLE    = "TIMES_NEW_ROMAN"
KEY_MARGIN_MM = 8.0


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate transform for key-map paper space
# ─────────────────────────────────────────────────────────────────────────────

def utm_to_keymap(x_utm, y_utm, vf_key, km_scale):
    b  = math.radians(vf_key.bearing_deg)
    dx = x_utm - vf_key.center_utm[0]
    dy = y_utm - vf_key.center_utm[1]
    s  =  dx * math.sin(b) + dy * math.cos(b)
    n  = -dx * math.cos(b) + dy * math.sin(b)
    return KM_VP_CX + s * km_scale, KM_VP_CY + n * km_scale


def in_plan_box(xp, yp, tol=1):
    return (KM_VP_X0 - tol <= xp <= KM_VP_X1 + tol and
            KM_VP_Y0 - tol <= yp <= KM_VP_Y1 + tol)


def _clip_to_plan(pts_paper):
    from shapely.geometry import LineString
    from shapely.geometry import box as shapely_box
    if len(pts_paper) < 2:
        return []
    rect    = shapely_box(KM_VP_X0, KM_VP_Y0, KM_VP_X1, KM_VP_Y1)
    clipped = LineString(pts_paper).intersection(rect)
    if clipped.is_empty:
        return []
    if clipped.geom_type == "LineString":
        return [list(clipped.coords)]
    if clipped.geom_type == "MultiLineString":
        return [list(g.coords) for g in clipped.geoms]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Overlay layers (same logic as make_sheet._draw_overlay_layers)
# ─────────────────────────────────────────────────────────────────────────────

def _draw_overlay_km(msp, doc, vf_key, km_scale, gdf_lu, gdf_bufs):
    plan_poly = ShapelyPolygon(vf_key.corners_utm)
    plan_bbox = plan_poly.bounds

    # ── Landuse ───────────────────────────────────────────────────────────────
    if gdf_lu is not None:
        if "PL_LANDUSE" not in [l.dxf.name for l in doc.layers]:
            doc.layers.add("PL_LANDUSE", color=LANDUSE_ACI_COLOR,
                           lineweight=LANDUSE_LINEWEIGHT)
        try:
            idx = list(gdf_lu.sindex.intersection(plan_bbox))
        except Exception:
            idx = list(range(len(gdf_lu)))
        for i in idx:
            geom = gdf_lu.iloc[i].geometry
            if geom is None or geom.is_empty:
                continue
            try:
                clipped = geom.intersection(plan_poly)
            except Exception:
                continue
            for ring in _geom_rings(clipped):
                if len(ring) < 2:
                    continue
                pts = [utm_to_keymap(x, y, vf_key, km_scale) for x, y in ring]
                for seg in _clip_to_plan(pts):
                    if len(seg) >= 2:
                        msp.add_lwpolyline(
                            seg,
                            dxfattribs={"layer": "PL_LANDUSE",
                                        "color": LANDUSE_ACI_COLOR,
                                        "lineweight": LANDUSE_LINEWEIGHT},
                        )

    # ── Buffers ───────────────────────────────────────────────────────────────
    for label, gdf_buf in (gdf_bufs or []):
        layer_name = f"PL_{label}"
        if layer_name not in [l.dxf.name for l in doc.layers]:
            doc.layers.add(layer_name, color=7, lineweight=BUFFER_LINEWEIGHT)
        for _, row in gdf_buf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty or not geom.intersects(plan_poly):
                continue
            try:
                clipped = geom.intersection(plan_poly)
            except Exception:
                continue
            for ring in _geom_rings(clipped):
                if len(ring) < 2:
                    continue
                pts = [utm_to_keymap(x, y, vf_key, km_scale) for x, y in ring]
                for seg in _clip_to_plan(pts):
                    if len(seg) >= 2:
                        ent = msp.add_lwpolyline(
                            seg,
                            dxfattribs={"layer": layer_name,
                                        "lineweight": BUFFER_LINEWEIGHT,
                                        "const_width": BUFFER_GLOBAL_W},
                        )
                        r, g, b = BUFFER_TRUE_COLOR
                        ent.rgb = (r, g, b)


# ─────────────────────────────────────────────────────────────────────────────
# Build key-map ViewFrame that best fits the full channel into VP_PLAN
# ─────────────────────────────────────────────────────────────────────────────

def _keymap_viewframe(geom):
    """
    Return (vf_key, km_scale) where vf_key is a synthetic ViewFrame whose
    bearing aligns the channel with the viewport long axis and whose
    along_m × perp_m fills VP_PLAN with KEY_MARGIN_MM margins.
    """
    coords  = list(geom.coords)
    dx = coords[-1][0] - coords[0][0]
    dy = coords[-1][1] - coords[0][1]
    bearing = math.degrees(math.atan2(dx, dy)) % 360

    center_pt = geom.interpolate(0.5, normalized=True)

    # Rotate all channel vertices to the bearing frame and find the
    # perpendicular extent of all view-frame corners.
    b = math.radians(bearing)
    sin_b, cos_b = math.sin(b), math.cos(b)

    def to_local(x, y):
        dx2 = x - center_pt.x; dy2 = y - center_pt.y
        s =  dx2 * sin_b + dy2 * cos_b   # along
        n = -dx2 * cos_b + dy2 * sin_b   # perp
        return s, n

    s_vals, n_vals = [], []
    for coord in coords:
        s, n = to_local(*coord)
        s_vals.append(s); n_vals.append(n)

    # Add frame corners' perp extent
    n_vals += [PLAN_PERP_M / 2, -PLAN_PERP_M / 2]

    s_span = max(s_vals) - min(s_vals)
    n_span = max(n_vals) - min(n_vals)

    # Add 10 % margin on all sides
    s_span *= 1.10
    n_span *= 1.10

    # Scale to fit VP_PLAN (maintain equal-scale: uniform scale for both axes)
    scale_s = (KM_VP_W - 2 * KEY_MARGIN_MM) / s_span
    scale_n = (KM_VP_H - 2 * KEY_MARGIN_MM) / n_span
    km_scale = min(scale_s, scale_n)

    along_m = (KM_VP_W - 2 * KEY_MARGIN_MM) / km_scale
    perp_m  = (KM_VP_H - 2 * KEY_MARGIN_MM) / km_scale

    # Re-centre: the channel midpoint in local s coords
    s_center = (max(s_vals) + min(s_vals)) / 2 * 0.0  # already 0-centred
    # Compute the geographic centre of the along extent
    # s_vals[:-2] excludes the added frame perp bounds; fall back to all s_vals
    s_core = s_vals[:-2] if len(s_vals) > 2 else s_vals
    n_core = n_vals[:-2] if len(n_vals) > 2 else n_vals
    s_mid = (max(s_core) + min(s_core)) / 2
    n_mid = (max(n_core) + min(n_core)) / 2

    # Shift centre point to match s_mid, n_mid in UTM
    cx = center_pt.x + s_mid * sin_b - n_mid * cos_b
    cy = center_pt.y + s_mid * cos_b + n_mid * sin_b

    corners = _frame_corners((cx, cy), bearing, along_m, perp_m)
    vf_key  = ViewFrame(
        index=0, c_start=0, c_end=geom.length,
        center_utm=(cx, cy),
        bearing_deg=bearing,
        along_m=along_m, perp_m=perp_m,
        corners_utm=corners,
    )
    return vf_key, km_scale


# ─────────────────────────────────────────────────────────────────────────────
# Draw content
# ─────────────────────────────────────────────────────────────────────────────

def _draw_frame_boxes(msp, doc, geom, frames, vf_key, km_scale):
    """Draw exact 1000 m frame rectangles, centered bold sheet numbers, and station labels."""
    FRAME_COLORS = [1, 2, 3, 4, 5, 6, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140]

    # Frame short side in paper space — use 55 % of it for the sheet number height
    frame_perp_paper = PLAN_PERP_M * km_scale
    num_text_h       = frame_perp_paper * 0.55   # bold & large, fits inside frame
    sta_text_h       = max(1.8, frame_perp_paper * 0.22)

    for vf in frames:
        col   = FRAME_COLORS[(vf.index - 1) % len(FRAME_COLORS)]
        b_rad = math.radians(vf.bearing_deg)
        s_vec = np.array([math.sin(b_rad), math.cos(b_rad)])

        # Frame corners at exact SEGMENT_LEN
        seg     = substring(geom, vf.c_start, vf.c_end)
        cpt     = seg.interpolate(0.5, normalized=True)
        seg_len = vf.c_end - vf.c_start
        corners = _frame_corners((cpt.x, cpt.y), vf.bearing_deg, seg_len, PLAN_PERP_M)
        box_pts = [utm_to_keymap(c[0], c[1], vf_key, km_scale)
                   for c in corners + [corners[0]]]

        # Draw frame rectangle
        for seg_pts in _clip_to_plan(box_pts):
            if len(seg_pts) >= 2:
                msp.add_lwpolyline(
                    seg_pts,
                    dxfattribs={"layer": "VF_FRAMES", "color": col,
                                "lineweight": 18},
                )

        # ── Sheet number: bold TNR, centered on frame midpoint ───────────────
        cx_paper, cy_paper = utm_to_keymap(cpt.x, cpt.y, vf_key, km_scale)
        rot_ang = (vf.bearing_deg - 90) % 360   # runs along channel axis
        if in_plan_box(cx_paper, cy_paper, tol=2):
            # MTEXT inline format: Times New Roman bold, middle-center attached
            content = (r"{\fTimes New Roman|b1|i0|c0|p0;#"
                       + str(vf.index) + "}")
            msp.add_mtext(
                content,
                dxfattribs={
                    "char_height": num_text_h,
                    "insert": (cx_paper, cy_paper),
                    "attachment_point": 5,        # MIDDLE_CENTER
                    "rotation": rot_ang,
                    "width": seg_len * km_scale * 0.85,
                    "layer": "VF_LABELS",
                    "color": col,
                },
            )

        # ── Station label outside the start edge ─────────────────────────────
        s_mid_x = (corners[0][0] + corners[3][0]) / 2
        s_mid_y = (corners[0][1] + corners[3][1]) / 2
        lsx, lsy = utm_to_keymap(s_mid_x, s_mid_y, vf_key, km_scale)
        off_x = lsx - s_vec[0] * km_scale * sta_text_h * 1.4
        off_y = lsy - s_vec[1] * km_scale * sta_text_h * 1.4
        if in_plan_box(off_x, off_y, tol=20):
            msp.add_text(
                vf.sta_start,
                dxfattribs={"layer": "VF_LABELS", "color": col,
                            "height": sta_text_h,
                            "rotation": rot_ang, "style": TEXT_STYLE,
                            "insert": (off_x, off_y)},
            ).set_placement((off_x, off_y),
                            align=TextEntityAlignment.MIDDLE_CENTER)


def _draw_channel(msp, gdf_all, channel_name, vf_key, km_scale):
    """Draw all channel alignments; highlight main channel."""
    for _, row in gdf_all.iterrows():
        g = row.geometry
        if g is None or g.is_empty or g.geom_type != "LineString":
            continue
        is_main = row["Name"].lower() == channel_name.lower()
        layer   = "KM_MAIN" if is_main else "KM_OTHER"
        col     = 1 if is_main else 253
        lw      = 50 if is_main else 13
        pts = [utm_to_keymap(x, y, vf_key, km_scale) for x, y in g.coords]
        for seg in _clip_to_plan(pts):
            if len(seg) >= 2:
                msp.add_lwpolyline(seg,
                    dxfattribs={"layer": layer, "color": col, "lineweight": lw})


def _draw_north_arrow_km(msp, vf_key):
    """Small north arrow in the top-right corner of the key-map area."""
    bearing_deg = vf_key.bearing_deg
    b_rad = math.radians(bearing_deg)
    n_arrow = np.array([math.cos(b_rad), math.sin(b_rad)])
    arrow_len = 6.0
    na_cx = KM_VP_X1 - 12.0
    na_cy = KM_VP_Y1 - 10.0
    tail_x = na_cx - n_arrow[0] * arrow_len / 2
    tail_y = na_cy - n_arrow[1] * arrow_len / 2
    head_x = na_cx + n_arrow[0] * arrow_len / 2
    head_y = na_cy + n_arrow[1] * arrow_len / 2
    msp.add_line((tail_x, tail_y), (head_x, head_y),
                 dxfattribs={"layer": "KM_LABELS", "color": 7, "lineweight": 35})
    perp = np.array([-n_arrow[1], n_arrow[0]])
    msp.add_lwpolyline(
        [(head_x, head_y),
         (head_x - n_arrow[0]*2.5 + perp[0]*1.2, head_y - n_arrow[1]*2.5 + perp[1]*1.2),
         (head_x - n_arrow[0]*2.5 - perp[0]*1.2, head_y - n_arrow[1]*2.5 - perp[1]*1.2),
         (head_x, head_y)],
        dxfattribs={"layer": "KM_LABELS", "color": 7}, close=True)
    msp.add_text("N", dxfattribs={
        "layer": "KM_LABELS", "color": 7, "height": 3.0,
        "style": TEXT_STYLE,
        "insert": (head_x + n_arrow[0]*1.8, head_y + n_arrow[1]*1.8)},
    ).set_placement((head_x + n_arrow[0]*1.8, head_y + n_arrow[1]*1.8),
                    align=TextEntityAlignment.MIDDLE_CENTER)


def _draw_scale_bar_km(msp, km_scale):
    """Scale bar showing a round distance at bottom-left of plan box."""
    # Pick a round distance that gives 20–60 mm bar
    candidates = [100, 200, 500, 1000, 2000, 5000]
    bar_m = candidates[0]
    for c in candidates:
        bar_mm = c * km_scale
        if 15 <= bar_mm <= 65:
            bar_m = c
            break

    bar_mm = bar_m * km_scale
    sb_x0 = KM_VP_X0 + 8
    sb_y  = KM_VP_Y0 + 5
    msp.add_lwpolyline(
        [(sb_x0, sb_y), (sb_x0 + bar_mm, sb_y)],
        dxfattribs={"layer": "KM_LABELS", "color": 7, "lineweight": 35})
    for tx in (sb_x0, sb_x0 + bar_mm):
        msp.add_line((tx, sb_y - 1.2), (tx, sb_y + 1.2),
                     dxfattribs={"layer": "KM_LABELS", "color": 7})
    label = f"{bar_m:,} m" if bar_m >= 1000 else f"{bar_m} m"
    msp.add_text(label, dxfattribs={
        "layer": "KM_LABELS", "color": 7, "height": 2.5,
        "style": TEXT_STYLE,
        "insert": (sb_x0 + bar_mm / 2, sb_y + 1.8)},
    ).set_placement((sb_x0 + bar_mm / 2, sb_y + 1.8),
                    align=TextEntityAlignment.BOTTOM_CENTER)


# ─────────────────────────────────────────────────────────────────────────────
# Title-block update
# ─────────────────────────────────────────────────────────────────────────────

def _update_title_km(pspace, channel_name, km_scale, n_sheets):
    title     = f"Key Map - {channel_name}   ({n_sheets} sheets)"
    h_sc      = _round_scale(round(1000 / km_scale))
    scale_txt = f"1:{h_sc}"
    today     = datetime.date.today().strftime("%d.%m.%Y")
    doc_no    = "2224-PD-HY-PP-B6-000"
    _once    = {"SCALE": scale_txt, "DOC_NO1": doc_no, "DRAWING-TITLE1": title}
    _written = set()

    for e in pspace:
        if e.dxftype() != "INSERT":
            continue
        try:
            for att in e.attribs:
                tag = att.dxf.get("tag", "")
                if tag in _once:
                    if tag not in _written:
                        att.dxf.text = _once[tag]
                        _written.add(tag)
                    else:
                        att.dxf.text = ""
                elif tag == "DATE":           att.dxf.text = today
                elif tag == "PROJECT-TITLE3": att.dxf.text = "Block 06"
                elif tag in ("REV_NO", "REV_NO1"): att.dxf.text = "1"
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def make_keymap(channel_name="Ais-CH1-FP", gdf_lu=None, gdf_bufs=None):
    print(f"\nBuilding key map for {channel_name} ...")

    gdf_all = gpd.read_file(SHP_PATH)
    row     = gdf_all[gdf_all["Name"].str.lower() == channel_name.lower()]
    if row.empty:
        raise ValueError(f"Channel '{channel_name}' not found.")
    geom = row.iloc[0].geometry

    _, frames = compute_view_frames(channel_name)
    vf_key, km_scale = _keymap_viewframe(geom)
    print(f"  Key-map bearing: {vf_key.bearing_deg:.1f}°  "
          f"scale 1:{round(1000/km_scale)}")

    # Satellite — JPEG for compact file size
    print("  Warping key-map satellite ...")
    px_w = int(KM_VP_W * 4)
    px_h = int(KM_VP_H * 4)
    warped = warp_satellite_to_frame(vf_key, out_w=px_w, out_h=px_h)
    version = int(time.time()) % 10000
    img_path = os.path.join(OUT_DXF, f"{channel_name}_KM_sat_v{version}.jpg")
    PILImage.fromarray(warped).save(img_path, "JPEG", quality=85)
    print(f"  Satellite saved: {img_path}")

    # Load template
    doc    = ezdxf.readfile(TMPL)
    pspace = doc.paperspace()
    if TEXT_STYLE not in doc.styles:
        doc.styles.add(TEXT_STYLE, font="times.ttf")

    # Remove content viewports, keep sheet border viewport (id=1)
    for vp in list(pspace.query("VIEWPORT")):
        if vp.dxf.get("id", 1) != 1:
            pspace.delete_entity(vp)

    # Remove template's decorative north arrow (compass rose block *U61)
    for e in list(pspace.query("INSERT")):
        if e.dxf.get("name", "") == "*U61":
            pspace.delete_entity(e)

    # Add layers
    for lname, col, lw in [
        ("SATELLITE",  7,  13),
        ("KM_OTHER",  253, 13),
        ("KM_MAIN",    1,  50),
        ("KM_LABELS",  7,  13),
        ("VF_FRAMES",  3,  18),
        ("VF_LABELS",  7,  13),
        ("KM_BORDER",  7,  35),
    ]:
        if lname not in [l.dxf.name for l in doc.layers]:
            doc.layers.add(lname, color=col, lineweight=lw)

    # ── Satellite image (fills full key-map area) ─────────────────────────────
    idef = doc.add_image_def(filename=img_path, size_in_pixel=(px_w, px_h))
    ie   = pspace.add_image(
        image_def=idef,
        insert=(KM_VP_X0, KM_VP_Y0),
        size_in_units=(KM_VP_W, KM_VP_H),
        dxfattribs={"layer": "SATELLITE"},
    )
    ie.dxf.flags = 7

    # ── Overlay vector layers ─────────────────────────────────────────────────
    _draw_overlay_km(pspace, doc, vf_key, km_scale, gdf_lu, gdf_bufs)

    # ── Channel alignments ────────────────────────────────────────────────────
    _draw_channel(pspace, gdf_all, channel_name, vf_key, km_scale)

    # ── View frame boxes + labels ─────────────────────────────────────────────
    _draw_frame_boxes(pspace, doc, geom, frames, vf_key, km_scale)

    # ── North arrow + scale bar ───────────────────────────────────────────────
    _draw_north_arrow_km(pspace, vf_key)
    _draw_scale_bar_km(pspace, km_scale)

    # ── Key-map border (full content area) ───────────────────────────────────
    pspace.add_lwpolyline(
        [(KM_VP_X0, KM_VP_Y0), (KM_VP_X1, KM_VP_Y0),
         (KM_VP_X1, KM_VP_Y1), (KM_VP_X0, KM_VP_Y1),
         (KM_VP_X0, KM_VP_Y0)],
        dxfattribs={"layer": "KM_BORDER", "color": 7, "lineweight": 35})

    # ── Title block ───────────────────────────────────────────────────────────
    _update_title_km(pspace, channel_name, km_scale, len(frames))

    # ── Save ──────────────────────────────────────────────────────────────────
    out = os.path.join(OUT_DXF, f"{channel_name}-KeyMap_v{version}.dxf")
    doc.saveas(out)
    print(f"  Saved: {out}")
    return out


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Ais-CH1-FP"
    make_keymap(name)
