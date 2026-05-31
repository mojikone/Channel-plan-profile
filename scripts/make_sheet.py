"""
Build one A1 plan+profile sheet DXF.

Profile layout:
  ┌─────────────────────────────────────────────────────────┐  ← VP_PROF_Y1
  │                                                         │
  │   CHART  (Crown/Invert/HGL/EGL/Ground)  CHART_H_PAPER  │
  │                                                         │
  ├─────────────────────────────────────────────────────────┤  ← prof_band_top
  │   BAND   (Annotation_Table)             BAND_H_PAPER    │
  └─────────────────────────────────────────────────────────┘  ← VP_PROF_Y0

  Y-scale: FIXED across all sheets (based on max segment range).
           Per-sheet: window CENTERED on segment data, same range.
  Band: separate scale (fixed paper height regardless of elevation scale).
"""
import os, sys, math, datetime, time
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import ezdxf
from ezdxf import units
from ezdxf.enums import TextEntityAlignment
import geopandas as gpd
from shapely.ops import substring
from shapely.geometry import LineString, box as shapely_box
from PIL import Image as PILImage

from config import (
    TMPL, DXF_DIR, SHP_PATH, OUT_DXF,
    VP_PLAN_X0, VP_PLAN_X1, VP_PLAN_Y0, VP_PLAN_Y1,
    VP_PLAN_W,  VP_PLAN_H,
    VP_PROF_X0, VP_PROF_X1, VP_PROF_Y0, VP_PROF_Y1,
    VP_PROF_W,  VP_PROF_H,
    SEGMENT_LEN, PLAN_ALONG_M, PLAN_PERP_M, PLAN_MARGIN_M,
    LAYER_COLORS, PROF_TEXT_H, PROF_X_MARGIN,
)
from view_frames import compute_view_frames, warp_satellite_to_frame

os.makedirs(OUT_DXF, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Profile layout constants
# ─────────────────────────────────────────────────────────────────────────────
BAND_H_PAPER   = 88.0            # mm allocated to annotation band
CHART_H_PAPER  = VP_PROF_H - BAND_H_PAPER   # mm for elevation chart
prof_band_top  = VP_PROF_Y0 + BAND_H_PAPER  # y (paper mm) at band/chart boundary

BAND_DXF_RANGE = 140.2           # DXF units from y=−140.2 to y=0
BAND_SCALE_Y   = BAND_H_PAPER / BAND_DXF_RANGE

# Fixed chart window (DXF units) — covers max segment range + margin
MAX_SEG_RANGE  = 90.0            # DXF units (8.7 m max actual + safety)
CHART_MARGIN   = 0.15            # 15 % margin top+bottom
CHART_WIN_DXF  = MAX_SEG_RANGE * (1 + 2 * CHART_MARGIN)   # ≈ 103.5 → use 110
CHART_WIN_DXF  = 110.0           # fixed, ~11 m window
CHART_SCALE_Y  = None

# Reserve a compact left column for band headings and the elevation axis.
PROF_LABEL_COL = 50.0
PROF_DATA_X0   = VP_PROF_X0 + PROF_LABEL_COL
PROF_DATA_X1   = VP_PROF_X1 - 10.0
PROF_DATA_W    = PROF_DATA_X1 - PROF_DATA_X0

# Profile x scale (same for chart and band)
PROF_SCALE_X   = PROF_DATA_W / (SEGMENT_LEN + 2 * PROF_X_MARGIN)
CHART_SCALE_Y  = PROF_SCALE_X
CHART_WIN_DXF  = CHART_H_PAPER / CHART_SCALE_Y

# Data layers vs band/grid layers
DATA_LAYERS    = {"Crown", "Invert", "Hydraulic_Grade",
                  "Energy_Grade", "Ground_Elevation"}
BAND_LAYER     = "Annotation_Table"
TEXT_STYLE     = "TIMES_NEW_ROMAN"

BAND_LABELS = {
    "Length (Unified) (m)": "Length (m)",
    "Slope (Calculated) (m/m)": "Slope (m/m)",
    "Elevation (Ground) (m)": "Ground Elev (m)",
    "Elevation (Invert) (m)": "Invert Elev (m)",
}

ANNOTATION_Y_OFFSETS = {
    "Rim:": -1.5,
    "Invert:": -3.0,
    "Box -": -1.5,
}

# Plan centre
VP_PLAN_CX     = (VP_PLAN_X0 + VP_PLAN_X1) / 2
VP_PLAN_CY     = (VP_PLAN_Y0 + VP_PLAN_Y1) / 2
PLAN_SCALE     = VP_PLAN_W / PLAN_ALONG_M

# ACI colour map (use entity's own colour if explicit, else layer mapping)
def _col(layer):
    for k, c in LAYER_COLORS.items():
        if k.lower() in layer.lower():
            return c
    return 7

def _entity_col(entity, layer):
    """Prefer entity-level explicit colour; fall back to layer mapping."""
    ec = entity.dxf.get("color", 256)
    if ec in (0, 256):          # ByBlock / ByLayer
        return _col(layer)
    return ec

def _text_h(layer):
    """Fixed paper height (mm) for a text entity by layer."""
    for k, h in sorted(PROF_TEXT_H.items(), key=lambda item: -len(item[0])):
        if k.lower() in layer.lower():
            return h
    return 2.0

import re as _re
def _station_text(chainage):
    """Return standard 0+050 station text from a DXF x-coordinate."""
    station = int(round(chainage))
    if station < 0:
        return None
    return f"{station // 1000}+{station % 1000:03d}"

# ─────────────────────────────────────────────────────────────────────────────
# 1.  Segment data-range scanner
# ─────────────────────────────────────────────────────────────────────────────

def _seg_y_range(profile_doc, c_start, c_end):
    """Return (y_center, y_lo, y_hi) in DXF units for the chart window."""
    ys = []
    for e in profile_doc.modelspace():
        layer = e.dxf.get("layer", "")
        if not any(dl.lower() in layer.lower() for dl in DATA_LAYERS):
            continue
        t = e.dxftype()
        if t == "LWPOLYLINE":
            for p in e.get_points():
                if c_start - 1 <= p[0] <= c_end + 1:
                    ys.append(p[1])
        elif t == "POLYLINE":
            for v in e.vertices:
                x, y = v.dxf.location[0], v.dxf.location[1]
                if c_start - 1 <= x <= c_end + 1:
                    ys.append(y)
    if not ys:
        return 400.0, 350.0, 460.0   # fallback
    y_mid = (min(ys) + max(ys)) / 2
    y_lo  = y_mid - CHART_WIN_DXF / 2
    y_hi  = y_mid + CHART_WIN_DXF / 2
    return y_mid, y_lo, y_hi


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Coordinate transforms
# ─────────────────────────────────────────────────────────────────────────────

def prof_to_paper(x_dxf, y_dxf, c_start, y_lo):
    """SewerGEMS DXF → profile paper-space mm."""
    xp = PROF_DATA_X0 + (x_dxf - (c_start - PROF_X_MARGIN)) * PROF_SCALE_X
    if y_dxf >= 0:
        # Chart area
        yp = prof_band_top + (y_dxf - y_lo) * CHART_SCALE_Y
    else:
        # Band area (y_dxf from −BAND_DXF_RANGE to 0)
        yp = VP_PROF_Y0 + (y_dxf + BAND_DXF_RANGE) * BAND_SCALE_Y
    return xp, yp


def _profile_text_rotation(angle_deg, y_dxf):
    """Convert source DXF rotation to the visible paper-space profile slope."""
    if y_dxf < 0:
        return angle_deg
    a = math.radians(angle_deg)
    return math.degrees(math.atan2(
        math.sin(a) * CHART_SCALE_Y,
        math.cos(a) * PROF_SCALE_X,
    ))


def _annotation_y_offset(text):
    for prefix, offset in ANNOTATION_Y_OFFSETS.items():
        if text.startswith(prefix):
            return offset
    return 0.0


def _clean_profile_annotation(text):
    if text.startswith("Box - "):
        return text[len("Box - "):]
    return text


def in_prof_box(xp, yp, tol=3):
    return (VP_PROF_X0 - tol <= xp <= VP_PROF_X1 + tol and
            VP_PROF_Y0 - tol <= yp <= VP_PROF_Y1 + tol)


def utm_to_plan(x_utm, y_utm, vf):
    b  = math.radians(vf.bearing_deg)
    dx = x_utm - vf.center_utm[0]
    dy = y_utm - vf.center_utm[1]
    s  =  dx * math.sin(b) + dy * math.cos(b)
    n  = -dx * math.cos(b) + dy * math.sin(b)
    return VP_PLAN_CX + s * PLAN_SCALE, VP_PLAN_CY + n * PLAN_SCALE


def in_plan_box(xp, yp, tol=1):
    return (VP_PLAN_X0 - tol <= xp <= VP_PLAN_X1 + tol and
            VP_PLAN_Y0 - tol <= yp <= VP_PLAN_Y1 + tol)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Profile entity copy
# ─────────────────────────────────────────────────────────────────────────────

def _copy_profile_entities(msp, profile_doc, c_start, c_end, y_lo):
    y_hi  = y_lo + CHART_WIN_DXF
    x_min = c_start - PROF_X_MARGIN
    x_max = c_end + PROF_X_MARGIN

    def _source_segments(raw, layer):
        """Clip source lines first so crossing grid and profile lines survive."""
        if len(raw) < 2:
            return []
        y_min = -BAND_DXF_RANGE if any(y < 0 for _, y in raw) else y_lo
        clipped = LineString(raw).intersection(
            shapely_box(x_min, y_min, x_max, y_hi)
        )
        if clipped.is_empty:
            return []
        if clipped.geom_type == "LineString":
            return [list(clipped.coords)]
        if clipped.geom_type == "MultiLineString":
            return [list(g.coords) for g in clipped.geoms]
        return []

    for entity in profile_doc.modelspace():
        t     = entity.dxftype()
        layer = entity.dxf.get("layer", "0")
        col   = _entity_col(entity, layer)

        if layer not in [l.dxf.name for l in msp.doc.layers]:
            msp.doc.layers.add(layer, color=_col(layer))

        # ── Polylines ────────────────────────────────────────────────────────
        if t in ("LWPOLYLINE", "POLYLINE"):
            if t == "LWPOLYLINE":
                raw = [(p[0], p[1]) for p in entity.get_points()]
            else:
                raw = [(v.dxf.location[0], v.dxf.location[1])
                       for v in entity.vertices]

            lw = entity.dxf.get("lineweight", 25)
            lw = max(13, lw) if lw > 0 else 25
            for pts in _source_segments(raw, layer):
                paper = [prof_to_paper(x, y, c_start, y_lo) for x, y in pts]
                if (layer == BAND_LAYER and len(paper) >= 2
                        and abs(paper[0][1] - paper[-1][1]) < 0.01):
                    edge = min(range(len(paper)), key=lambda i: paper[i][0])
                    paper[edge] = (VP_PROF_X0, paper[edge][1])
                if len(paper) >= 2:
                    msp.add_lwpolyline(
                        paper,
                        dxfattribs={"layer": layer, "color": col,
                                    "lineweight": lw},
                    )

        # ── Text ─────────────────────────────────────────────────────────────
        elif t == "TEXT":
            ins = entity.dxf.get("insert", (0, 0, 0))
            x, y = ins[0], ins[1]

            # X window check
            if not (x_min - 5 <= x <= x_max + 5):
                continue

            # Y window check
            if y >= 0 and not (y_lo - 2 <= y <= y_hi + 2):
                continue
            if y < 0 and not (-BAND_DXF_RANGE - 2 <= y <= 2):
                continue

            val = entity.dxf.get("text", "")
            if not val:
                continue
            source_val = val
            val = BAND_LABELS.get(val, val)
            val = _clean_profile_annotation(val)

            # 50 m station filter for the bottom axis / band station row
            if "Grid_Text" in layer or "Annotation_Table" in layer:
                if _re.match(r"^-?\d+\+\d+$", val.strip()):
                    if int(round(x)) % 50:
                        continue
                    val = _station_text(x)
                    if val is None:
                        continue

            xp, yp = prof_to_paper(x, y, c_start, y_lo)
            if layer == "Annotation":
                yp += _annotation_y_offset(source_val)
            # Keep text inside profile box (generous tolerance for labels)
            if not (VP_PROF_X0 - 5 <= xp <= VP_PROF_X1 + 5
                    and VP_PROF_Y0 - 3 <= yp <= VP_PROF_Y1 + 5):
                continue

            h_paper = _text_h(layer)
            ang     = entity.dxf.get("rotation", 0.0)
            if layer == "Annotation":
                ang = _profile_text_rotation(ang, y)
            source_xp = xp
            heading = layer == BAND_LAYER and x < 0
            if heading:
                xp = PROF_DATA_X0 - 5.0
            xp = min(max(xp, VP_PROF_X0 + 2.0), VP_PROF_X1 - 2.0)
            text_attribs = {"layer": layer, "color": col,
                            "height": h_paper, "rotation": ang,
                            "style": TEXT_STYLE, "insert": (xp, yp)}
            halign = entity.dxf.get("halign", 0)
            valign = entity.dxf.get("valign", 0)
            if heading:
                halign, valign = 2, 2
            if VP_PROF_X0 + 2.0 <= source_xp <= VP_PROF_X1 - 2.0:
                text_attribs.update({"halign": halign, "valign": valign})
                if halign or valign:
                    text_attribs["align_point"] = (xp, yp)
            msp.add_text(val, dxfattribs=text_attribs)

        # ── Lines ─────────────────────────────────────────────────────────────
        elif t == "LINE":
            x0, y0 = entity.dxf.start[0], entity.dxf.start[1]
            x1, y1 = entity.dxf.end[0],   entity.dxf.end[1]
            for pts in _source_segments([(x0, y0), (x1, y1)], layer):
                (xp0, yp0), (xp1, yp1) = [
                    prof_to_paper(x, y, c_start, y_lo) for x, y in pts
                ]
                msp.add_line((xp0, yp0), (xp1, yp1),
                             dxfattribs={"layer": layer, "color": col})

    # ── Elevation axis labels (own computed labels so they match the window) ──
    elev_lo_m = math.ceil(y_lo / 10 / 2) * 2
    elev_hi_m = math.floor(y_hi      / 10 / 2) * 2
    e = elev_lo_m
    while e <= elev_hi_m:
        _, yp = prof_to_paper(c_start, e * 10, c_start, y_lo)
        if prof_band_top <= yp <= VP_PROF_Y1:
            msp.add_text(
                f"{e:.2f}",
                dxfattribs={"layer": "Grid_Text", "color": 7,
                            "height": 3.5, "style": TEXT_STYLE,
                            "insert": (PROF_DATA_X0 - 5, yp)},
            ).set_placement((PROF_DATA_X0 - 5, yp),
                            align=TextEntityAlignment.MIDDLE_RIGHT)
        e += 2

    # ── Profile frame boxes ───────────────────────────────────────────────────
    for pts in [
        [(VP_PROF_X0, VP_PROF_Y0), (VP_PROF_X1, VP_PROF_Y0),
         (VP_PROF_X1, VP_PROF_Y1), (VP_PROF_X0, VP_PROF_Y1),
         (VP_PROF_X0, VP_PROF_Y0)],
        [(VP_PROF_X0, prof_band_top), (VP_PROF_X1, prof_band_top)],
        [(PROF_DATA_X0, VP_PROF_Y0), (PROF_DATA_X0, VP_PROF_Y1)],
        [(PROF_DATA_X1, VP_PROF_Y0), (PROF_DATA_X1, VP_PROF_Y1)],
    ]:
        msp.add_lwpolyline(pts, dxfattribs={"layer": "0", "color": 7,
                                             "lineweight": 35})


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Plan view
# ─────────────────────────────────────────────────────────────────────────────

def _clip_polyline_to_plan(pts_paper):
    """Clip a list of (x,y) paper-space points to the plan box using shapely."""
    if len(pts_paper) < 2:
        return []
    plan_rect = shapely_box(VP_PLAN_X0, VP_PLAN_Y0, VP_PLAN_X1, VP_PLAN_Y1)
    line      = LineString(pts_paper)
    clipped   = line.intersection(plan_rect)
    if clipped.is_empty:
        return []
    if clipped.geom_type == "LineString":
        return [list(clipped.coords)]
    if clipped.geom_type == "MultiLineString":
        return [list(g.coords) for g in clipped.geoms]
    return []


def _draw_plan(msp, doc, vf, geom, gdf, channel_name,
               img_path, img_w, img_h):

    for lname, col, lw in [
        ("SATELLITE",   7,  13),
        ("PL_OTHER",  253,  13),
        ("PL_MAIN",     1,  50),
        ("PL_MARGIN",   8,  25),
        ("PL_STA",      7,  35),
        ("PL_LABEL",    7,  13),
        ("PL_BORDER",   7,  35),
    ]:
        if lname not in [l.dxf.name for l in doc.layers]:
            doc.layers.add(lname, color=col, lineweight=lw)

    # ── Satellite image (fills plan box exactly) ──────────────────────────────
    if img_path and os.path.exists(img_path):
        idef = doc.add_image_def(filename=img_path,
                                  size_in_pixel=(img_w, img_h))
        ie = msp.add_image(
            image_def=idef,
            insert=(VP_PLAN_X0, VP_PLAN_Y0),
            size_in_units=(VP_PLAN_W, VP_PLAN_H),
            dxfattribs={"layer": "SATELLITE"},
        )
        ie.dxf.flags = 7

    # ── Other channels — clipped to plan box ─────────────────────────────────
    for _, row in gdf.iterrows():
        if row["Name"].lower() == channel_name.lower():
            continue
        g = row.geometry
        if g is None or g.is_empty or g.geom_type != "LineString":
            continue
        pts_paper = [utm_to_plan(x, y, vf) for x, y in g.coords]
        for seg in _clip_polyline_to_plan(pts_paper):
            if len(seg) >= 2:
                msp.add_lwpolyline(
                    seg, dxfattribs={"layer": "PL_OTHER", "color": 253,
                                     "lineweight": 13})

    # ── Main channel: margin sections (lighter) ───────────────────────────────
    for d0, d1, layer, col in [
        (max(0.0, vf.c_start - PLAN_MARGIN_M), vf.c_start, "PL_MARGIN", 8),
        (vf.c_end, min(geom.length, vf.c_end + PLAN_MARGIN_M), "PL_MARGIN", 8),
    ]:
        if d1 > d0:
            seg = substring(geom, d0, d1)
            pts = [utm_to_plan(x, y, vf) for x, y in seg.coords]
            for clipped in _clip_polyline_to_plan(pts):
                if len(clipped) >= 2:
                    msp.add_lwpolyline(clipped,
                                       dxfattribs={"layer": layer, "color": col,
                                                   "lineweight": 25})

    # ── Main channel: core segment (bold red) ─────────────────────────────────
    core = substring(geom, vf.c_start, vf.c_end)
    pts  = [utm_to_plan(x, y, vf) for x, y in core.coords]
    for clipped in _clip_polyline_to_plan(pts):
        if len(clipped) >= 2:
            msp.add_lwpolyline(clipped,
                               dxfattribs={"layer": "PL_MAIN", "color": 1,
                                           "lineweight": 50})

    # ── Station ticks every 50 m  (format 0+050) ─────────────────────────────
    tick_half = 4.0   # mm half-tick
    label_gap = 4.0   # mm clear margin beyond the tick

    d = max(0.0, vf.c_start - PLAN_MARGIN_M)
    d_end = min(geom.length, vf.c_end + PLAN_MARGIN_M)
    while d <= d_end + 0.01:
        p      = geom.interpolate(d)
        xp, yp = utm_to_plan(p.x, p.y, vf)
        if in_plan_box(xp, yp):
            delta = min(2.0, max(d, geom.length - d, 0.1))
            p0 = geom.interpolate(max(0.0, d - delta))
            p1 = geom.interpolate(min(geom.length, d + delta))
            q0 = np.array(utm_to_plan(p0.x, p0.y, vf))
            q1 = np.array(utm_to_plan(p1.x, p1.y, vf))
            tangent = q1 - q0
            tangent /= np.linalg.norm(tangent)
            normal = np.array([-tangent[1], tangent[0]])
            # Tick line — perpendicular to channel (vertical in plan space)
            msp.add_line(
                (xp - normal[0] * tick_half, yp - normal[1] * tick_half),
                (xp + normal[0] * tick_half, yp + normal[1] * tick_half),
                dxfattribs={"layer": "PL_STA", "color": 7, "lineweight": 25},
            )
            # Label: 0+050 format
            km      = int(d // 1000)
            rem     = int(round(d % 1000))
            sta_lbl = f"{km}+{rem:03d}"

            # ── Text perpendicular to alignment ──────────────────────────
            # Channel is horizontal in plan space → perpendicular = 90°
            # Offset label to the +n (left) side of the alignment
            # Center the label on the tick axis. Offset its midpoint far enough
            # that the nearest text edge retains a clear gap from the tick end.
            text_height = 6.0
            text_span = len(sta_lbl) * text_height * 0.62
            label_offset = tick_half + label_gap + text_span / 2
            lx = xp + normal[0] * label_offset
            ly = yp + normal[1] * label_offset
            text_rotation = math.degrees(math.atan2(normal[1], normal[0]))
            msp.add_text(
                sta_lbl,
                dxfattribs={"layer": "PL_LABEL", "color": 7,
                            "height": text_height,
                            "rotation": text_rotation, "style": TEXT_STYLE,
                            "insert": (lx, ly)},
            ).set_placement((lx, ly), align=TextEntityAlignment.MIDDLE_CENTER)
        d += 50.0

    # ── North arrow (true north in rotated plan) ──────────────────────────────
    # True north in plan paper space: direction = bearing_deg from horizontal
    b_rad    = math.radians(vf.bearing_deg)
    # Unit vector pointing True North in paper-space coords:
    # North in UTM = (0,1); in plan local: s=cos(b), n=sin(b)
    n_arrow  = np.array([math.cos(b_rad), math.sin(b_rad)])  # (s_north, n_north)
    # Paper direction: x += s_north*scale_x  (but scale is uniform = PLAN_SCALE)
    # → just use (s, n) as a direction in paper coords directly
    arrow_len = 8.0      # mm
    na_cx     = VP_PLAN_X1 - 14.0
    na_cy     = VP_PLAN_Y1 - 12.0
    # Arrow shaft
    tail_x = na_cx - n_arrow[0] * arrow_len / 2
    tail_y = na_cy - n_arrow[1] * arrow_len / 2
    head_x = na_cx + n_arrow[0] * arrow_len / 2
    head_y = na_cy + n_arrow[1] * arrow_len / 2
    msp.add_line((tail_x, tail_y), (head_x, head_y),
                 dxfattribs={"layer": "PL_LABEL", "color": 7, "lineweight": 35})
    # Arrowhead
    perp = np.array([-n_arrow[1], n_arrow[0]])
    msp.add_lwpolyline(
        [(head_x, head_y),
         (head_x - n_arrow[0]*3 + perp[0]*1.5,
          head_y - n_arrow[1]*3 + perp[1]*1.5),
         (head_x - n_arrow[0]*3 - perp[0]*1.5,
          head_y - n_arrow[1]*3 - perp[1]*1.5),
         (head_x, head_y)],
        dxfattribs={"layer": "PL_LABEL", "color": 7},
        close=True,
    )
    # "N" label at head
    msp.add_text(
        "N",
        dxfattribs={"layer": "PL_LABEL", "color": 7, "height": 3.5,
                    "style": TEXT_STYLE,
                    "insert": (head_x + n_arrow[0]*2, head_y + n_arrow[1]*2)},
    ).set_placement(
        (head_x + n_arrow[0]*2, head_y + n_arrow[1]*2),
        align=TextEntityAlignment.MIDDLE_CENTER,
    )

    # ── Scale bar ─────────────────────────────────────────────────────────────
    sb_x0, sb_y = VP_PLAN_X0 + 8, VP_PLAN_Y0 + 6
    bar_mm      = 100 * PLAN_SCALE   # 100 m in paper mm
    msp.add_lwpolyline(
        [(sb_x0, sb_y), (sb_x0 + bar_mm, sb_y)],
        dxfattribs={"layer": "PL_LABEL", "color": 7, "lineweight": 35})
    for tx in (sb_x0, sb_x0 + bar_mm):
        msp.add_line((tx, sb_y - 1.5), (tx, sb_y + 1.5),
                     dxfattribs={"layer": "PL_LABEL", "color": 7})
    msp.add_text(
        "100 m",
        dxfattribs={"layer": "PL_LABEL", "color": 7, "height": 2.5,
                    "style": TEXT_STYLE,
                    "insert": (sb_x0 + bar_mm / 2, sb_y + 2)},
    ).set_placement(
        (sb_x0 + bar_mm / 2, sb_y + 2), align=TextEntityAlignment.BOTTOM_CENTER)

    # Plan border on top of everything
    msp.add_lwpolyline(
        [(VP_PLAN_X0, VP_PLAN_Y0), (VP_PLAN_X1, VP_PLAN_Y0),
         (VP_PLAN_X1, VP_PLAN_Y1), (VP_PLAN_X0, VP_PLAN_Y1),
         (VP_PLAN_X0, VP_PLAN_Y0)],
        dxfattribs={"layer": "PL_BORDER", "color": 7, "lineweight": 35})


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Title block ATTRIB update
# ─────────────────────────────────────────────────────────────────────────────

def _update_title(pspace, channel_name, vf, n_sheets):
    title = (f"Plan & Profile Of {channel_name}  "
             f"{vf.sta_start} - {vf.sta_end}     "
             f"Sheet #{vf.index} of {n_sheets}")
    h_sc  = round(1000 / PLAN_SCALE)
    # V scale: 1 m elev = CHART_SCALE_Y × 10 mm → V = 1000/(10×CHART_SCALE_Y)
    v_sc  = round(1000 / (10 * CHART_SCALE_Y) / 5) * 5
    scale = f"H 1:{h_sc}   V 1:{v_sc}"
    today = datetime.date.today().strftime("%d.%m.%Y")

    for e in pspace:
        if e.dxftype() != "INSERT":
            continue
        try:
            for att in e.attribs:
                tag = att.dxf.get("tag", "")
                if   tag == "DRAWING-TITLE1": att.dxf.text = title
                elif tag == "SCALE":          att.dxf.text = scale
                elif tag == "DATE":           att.dxf.text = today
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Main builder
# ─────────────────────────────────────────────────────────────────────────────

def make_sheet(channel_name="Ais-CH1-FP", sheet_index=1):
    print(f"\nBuilding {channel_name} Sheet #{sheet_index} ...")

    gdf     = gpd.read_file(SHP_PATH)
    row     = gdf[gdf["Name"].str.lower() == channel_name.lower()]
    if row.empty:
        raise ValueError(f"Channel '{channel_name}' not found.")
    geom    = row.iloc[0].geometry

    prof_path = os.path.join(DXF_DIR, f"{channel_name}.dxf")
    if not os.path.exists(prof_path):
        raise FileNotFoundError(f"Profile DXF not found: {prof_path}")
    print("  Loading profile DXF...")
    prof_doc = ezdxf.readfile(prof_path)

    _, frames = compute_view_frames(channel_name)
    vf        = frames[sheet_index - 1]
    print(f"  View frame: {vf}")

    # Determine profile y window for this segment
    _, y_lo, y_hi = _seg_y_range(prof_doc, vf.c_start, vf.c_end)
    print(f"  Profile y window: {y_lo/10:.1f}–{y_hi/10:.1f} m elevation")

    # Warp satellite
    print("  Warping satellite...")
    px_w = int(VP_PLAN_W * 4)
    px_h = int(VP_PLAN_H * 4)
    warped   = warp_satellite_to_frame(vf, out_w=px_w, out_h=px_h)
    version = int(time.time()) % 10000
    img_path = os.path.join(
        OUT_DXF, f"{channel_name}_S{sheet_index:02d}_sat_v{version}.png"
    )
    PILImage.fromarray(warped).save(img_path, "PNG")
    print(f"  Satellite saved: {img_path}")

    # Load template
    doc    = ezdxf.readfile(TMPL)
    pspace = doc.paperspace()
    if TEXT_STYLE not in doc.styles:
        doc.styles.add(TEXT_STYLE, font="times.ttf")

    # The template's plan/profile content viewports overlap the generated
    # paper-space layout. Keep only the required overall paper viewport.
    for viewport in list(pspace.query("VIEWPORT")):
        if viewport.dxf.get("id", 1) != 1:
            pspace.delete_entity(viewport)

    # Update title block
    _update_title(pspace, channel_name, vf, len(frames))

    # Ensure needed layers exist
    for layer, col in LAYER_COLORS.items():
        if layer not in [l.dxf.name for l in doc.layers]:
            doc.layers.add(layer, color=col)

    # Draw profile
    print("  Drawing profile...")
    _copy_profile_entities(pspace, prof_doc, vf.c_start, vf.c_end, y_lo)

    # Draw plan
    print("  Drawing plan...")
    _draw_plan(pspace, doc, vf, geom, gdf, channel_name,
               img_path, px_w, px_h)

    # Save
    out = os.path.join(OUT_DXF, f"{channel_name}-Sheet{sheet_index:02d}_v{version}.dxf")
    doc.saveas(out)
    print(f"  Saved: {out}")
    return out


if __name__ == "__main__":
    ch  = sys.argv[1] if len(sys.argv) > 1 else "Ais-CH1-FP"
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    make_sheet(ch, idx)
