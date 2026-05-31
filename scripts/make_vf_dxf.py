"""
Export the view frame layout as a DXF in real UTM coordinates.
Open in AutoCAD / Civil 3D to verify frame alignment with the channel.

Layers:
  VF_FRAMES      – frame rectangles (one per sheet, numbered)
  VF_LABELS      – station labels at frame start/end edges
  CH_MAIN        – Ais-CH1-FP alignment (highlighted)
  CH_OTHER       – all other channels (grey)
  CH_STATIONS    – station tick marks every 1000 m
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(__file__))

import ezdxf
from ezdxf import units
from ezdxf.enums import TextEntityAlignment
import numpy as np
import geopandas as gpd
from shapely.ops import substring

from config import SHP_PATH, OUT_VF, SEGMENT_LEN, PLAN_ALONG_M, PLAN_PERP_M

os.makedirs(OUT_VF, exist_ok=True)


# ── ACI colour helpers ────────────────────────────────────────────────────────
# 18 distinct ACI colours for frame boxes (cycling through tab-like palette)
FRAME_COLORS = [1, 2, 3, 4, 5, 6, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140]


def _bearing_vecs(bearing_deg):
    b = math.radians(bearing_deg)
    s = np.array([math.sin(b), math.cos(b)])   # along-channel unit vector
    n = np.array([-math.cos(b), math.sin(b)])  # left-perp unit vector
    return s, n


def _frame_corners(center_xy, bearing_deg, along_m, perp_m):
    """4 corners of a bearing-aligned rectangle (exact extent, no margin)."""
    s, n = _bearing_vecs(bearing_deg)
    c = np.array(center_xy)
    hs, hn = along_m / 2, perp_m / 2
    return [
        tuple(c - hs*s - hn*n),   # start / right  (SW in map terms)
        tuple(c + hs*s - hn*n),   # end   / right  (SE)
        tuple(c + hs*s + hn*n),   # end   / left   (NE)
        tuple(c - hs*s + hn*n),   # start / left   (NW)
    ]


def make_vf_dxf(channel_name="Ais-CH1-FP"):
    gdf  = gpd.read_file(SHP_PATH)
    row  = gdf[gdf["Name"].str.lower() == channel_name.lower()]
    if row.empty:
        raise ValueError(f"Channel '{channel_name}' not found.")
    geom  = row.iloc[0].geometry
    total = geom.length

    # ── DXF setup ─────────────────────────────────────────────────────────────
    doc = ezdxf.new("R2010")
    doc.units = units.M
    msp = doc.modelspace()

    # Layers
    doc.layers.add("CH_OTHER",    color=253, lineweight=13)
    doc.layers.add("CH_MAIN",     color=1,   lineweight=50)   # red, thick
    doc.layers.add("CH_STATIONS", color=7,   lineweight=13)
    doc.layers.add("VF_FRAMES",   color=3,   lineweight=25)
    doc.layers.add("VF_LABELS",   color=7,   lineweight=13)
    doc.layers.add("VF_BEARING",  color=4,   lineweight=13)   # bearing arrows

    # ── Other channels ────────────────────────────────────────────────────────
    for _, r in gdf.iterrows():
        if r["Name"].lower() == channel_name.lower():
            continue
        g = r.geometry
        if g is None or g.is_empty:
            continue
        coords = list(g.coords) if g.geom_type == "LineString" else []
        if len(coords) >= 2:
            msp.add_lwpolyline(
                [(x, y) for x, y in coords],
                dxfattribs={"layer": "CH_OTHER", "color": 253},
            )

    # ── Main channel ──────────────────────────────────────────────────────────
    msp.add_lwpolyline(
        [(x, y) for x, y in geom.coords],
        dxfattribs={"layer": "CH_MAIN", "color": 1},
    )

    # ── View frames ───────────────────────────────────────────────────────────
    n_sheets = math.ceil(total / SEGMENT_LEN)
    text_h   = max(20.0, PLAN_PERP_M * 0.06)   # readable at overview scale

    for i in range(n_sheets):
        c_start = i * SEGMENT_LEN
        c_end   = min((i + 1) * SEGMENT_LEN, total)
        seg     = substring(geom, c_start, c_end)

        # Local tangent bearing at midpoint
        mid_d = c_start + (c_end - c_start) / 2
        delta = min(5.0, (c_end - c_start) * 0.04)
        p0 = geom.interpolate(mid_d - delta)
        p1 = geom.interpolate(mid_d + delta)
        bearing_deg = math.degrees(math.atan2(p1.x - p0.x, p1.y - p0.y)) % 360

        center_pt = geom.interpolate(mid_d)
        cx, cy    = center_pt.x, center_pt.y

        # Frame rectangle (exact SEGMENT_LEN, no extra margin)
        corners = _frame_corners((cx, cy), bearing_deg, SEGMENT_LEN, PLAN_PERP_M)
        box_pts = corners + [corners[0]]   # closed polygon

        col = FRAME_COLORS[i % len(FRAME_COLORS)]

        # Draw frame
        msp.add_lwpolyline(
            [(c[0], c[1]) for c in box_pts],
            dxfattribs={"layer": "VF_FRAMES", "color": col, "lineweight": 25},
        )

        # ── Station labels at START edge (between corners[0] and corners[3]) ──
        sta_start = f"{int(c_start // 1000)}+{int(c_start % 1000):03d}"
        sta_end   = f"{int(c_end   // 1000)}+{int(c_end   % 1000):03d}"

        start_mid_x = (corners[0][0] + corners[3][0]) / 2
        start_mid_y = (corners[0][1] + corners[3][1]) / 2
        end_mid_x   = (corners[1][0] + corners[2][0]) / 2
        end_mid_y   = (corners[1][1] + corners[2][1]) / 2

        # Shift label slightly outside the frame along the start edge normal
        s_vec, _ = _bearing_vecs(bearing_deg)
        lbl_offset = text_h * 1.2

        # Start label (just outside the start edge, away from frame)
        lx0 = start_mid_x - s_vec[0] * lbl_offset
        ly0 = start_mid_y - s_vec[1] * lbl_offset
        msp.add_text(
            f"#{i+1}  {sta_start}",
            dxfattribs={
                "layer": "VF_LABELS", "color": col,
                "height": text_h,
                "rotation": (bearing_deg - 90) % 360,
                "insert": (lx0, ly0),
            },
        ).set_placement(
            (lx0, ly0), align=TextEntityAlignment.MIDDLE_CENTER
        )

        # End label (just outside the end edge)
        lx1 = end_mid_x + s_vec[0] * lbl_offset
        ly1 = end_mid_y + s_vec[1] * lbl_offset
        msp.add_text(
            sta_end,
            dxfattribs={
                "layer": "VF_LABELS", "color": col,
                "height": text_h * 0.85,
                "rotation": (bearing_deg - 90) % 360,
                "insert": (lx1, ly1),
            },
        ).set_placement(
            (lx1, ly1), align=TextEntityAlignment.MIDDLE_CENTER
        )

        # Sheet number inside frame near start
        sheet_lx = cx - s_vec[0] * (SEGMENT_LEN * 0.3)
        sheet_ly = cy - s_vec[1] * (SEGMENT_LEN * 0.3)
        msp.add_text(
            f"SH-{i+1:02d}",
            dxfattribs={
                "layer": "VF_LABELS", "color": col,
                "height": text_h * 1.2,
                "rotation": (bearing_deg - 90) % 360,
                "insert": (sheet_lx, sheet_ly),
            },
        ).set_placement(
            (sheet_lx, sheet_ly), align=TextEntityAlignment.MIDDLE_CENTER
        )

        # ── Bearing arrow at midpoint ─────────────────────────────────────────
        arr_len = PLAN_PERP_M * 0.3
        ax0, ay0 = cx - s_vec[0] * arr_len / 2, cy - s_vec[1] * arr_len / 2
        ax1, ay1 = cx + s_vec[0] * arr_len / 2, cy + s_vec[1] * arr_len / 2
        msp.add_line(
            (ax0, ay0), (ax1, ay1),
            dxfattribs={"layer": "VF_BEARING", "color": 4},
        )

        # ── Station tick on the channel ───────────────────────────────────────
        p_sta = geom.interpolate(c_start)
        _, n_vec = _bearing_vecs(bearing_deg)
        tick = PLAN_PERP_M * 0.15
        msp.add_line(
            (p_sta.x - n_vec[0] * tick, p_sta.y - n_vec[1] * tick),
            (p_sta.x + n_vec[0] * tick, p_sta.y + n_vec[1] * tick),
            dxfattribs={"layer": "CH_STATIONS", "color": 7, "lineweight": 35},
        )

    # Final tick at channel end
    p_end = geom.interpolate(total)
    msp.add_line(
        (p_end.x - 30, p_end.y), (p_end.x + 30, p_end.y),
        dxfattribs={"layer": "CH_STATIONS", "color": 7},
    )

    # ── Save ──────────────────────────────────────────────────────────────────
    out = os.path.join(OUT_VF, f"{channel_name}_view_frames_v2.dxf")
    doc.saveas(out)
    print(f"Saved: {out}")
    return out


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Ais-CH1-FP"
    make_vf_dxf(name)
