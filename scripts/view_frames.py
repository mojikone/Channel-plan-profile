"""
Compute and visualise view frames along a channel alignment.
A view frame = one sheet's plan window: bearing-aligned rectangle,
1000 m along-channel × perpendicular extent from viewport aspect ratio.

Usage:
    python view_frames.py Ais-CH1-FP
"""
import os, sys, math
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon as MplPolygon
import geopandas as gpd
from shapely.ops import substring
from shapely.geometry import LineString, Point
from pyproj import Transformer

from config import (
    SHP_PATH, OUT_VF, IMG_DIR,
    SEGMENT_LEN, PLAN_ALONG_M, PLAN_PERP_M,
    ESRI_TILE_URL, TILE_SIZE, SAT_TILE_QUALITY,
)

os.makedirs(OUT_VF, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# 1.  Core data structures
# ══════════════════════════════════════════════════════════════════════════════

class ViewFrame:
    """One plan-view frame aligned to the channel bearing."""
    def __init__(self, index, c_start, c_end, center_utm, bearing_deg,
                 along_m, perp_m, corners_utm):
        self.index       = index          # 1-based sheet number
        self.c_start     = c_start        # chainage start  (m)
        self.c_end       = c_end          # chainage end    (m)
        self.center_utm  = center_utm     # (x, y) UTM
        self.bearing_deg = bearing_deg    # degrees from North, clockwise
        self.along_m     = along_m        # total along-channel extent (m) shown
        self.perp_m      = perp_m         # total perpendicular extent (m) shown
        self.corners_utm = corners_utm    # 4 UTM corners of frame rectangle (CCW)

    @property
    def sta_start(self):
        return f"{int(self.c_start // 1000)}+{int(self.c_start % 1000):03d}"

    @property
    def sta_end(self):
        return f"{int(self.c_end   // 1000)}+{int(self.c_end   % 1000):03d}"

    def __repr__(self):
        return (f"VF#{self.index:02d}  {self.sta_start}–{self.sta_end}"
                f"  brg={self.bearing_deg:.1f}°"
                f"  ctr=({self.center_utm[0]:.0f},{self.center_utm[1]:.0f})")


# ══════════════════════════════════════════════════════════════════════════════
# 2.  Geometry helpers
# ══════════════════════════════════════════════════════════════════════════════

def _mean_bearing(geom_segment):
    """Mean bearing (deg from North, CW) of a LineString segment."""
    coords = list(geom_segment.coords)
    # Use start → end for overall direction
    dx = coords[-1][0] - coords[0][0]
    dy = coords[-1][1] - coords[0][1]
    rad = math.atan2(dx, dy)           # atan2(east, north) = bearing from North
    return math.degrees(rad) % 360


def _frame_corners(center_xy, bearing_deg, along_m, perp_m):
    """
    Return 4 corners (UTM) of a bearing-aligned rectangle.
    along_m: full length along bearing direction
    perp_m : full width perpendicular to bearing
    """
    b  = math.radians(bearing_deg)
    # unit vectors: along-bearing (s) and left-perpendicular (n)
    s_vec = np.array([math.sin(b),  math.cos(b)])   # East, North components
    n_vec = np.array([-math.cos(b), math.sin(b)])   # left of travel

    cx, cy = center_xy
    c = np.array([cx, cy])
    corners = [
        c - (along_m/2)*s_vec - (perp_m/2)*n_vec,   # SW
        c + (along_m/2)*s_vec - (perp_m/2)*n_vec,   # SE
        c + (along_m/2)*s_vec + (perp_m/2)*n_vec,   # NE
        c - (along_m/2)*s_vec + (perp_m/2)*n_vec,   # NW
    ]
    return [tuple(c) for c in corners]


# ══════════════════════════════════════════════════════════════════════════════
# 3.  Compute view frames
# ══════════════════════════════════════════════════════════════════════════════

def compute_view_frames(channel_name, seg_len=SEGMENT_LEN,
                        along_m=PLAN_ALONG_M, perp_m=PLAN_PERP_M):
    """
    Walk the channel alignment and build a ViewFrame for every segment.
    Returns (geom_full, [ViewFrame, ...])
    """
    gdf  = gpd.read_file(SHP_PATH)
    row  = gdf[gdf["Name"].str.lower() == channel_name.lower()]
    if row.empty:
        raise ValueError(f"Channel '{channel_name}' not found in shapefile.")

    geom  = row.iloc[0].geometry
    # Channels whose shapefile digitisation runs opposite to the profile DXF direction
    REVERSED_CHANNELS = {"Taw-CH5-SM"}
    if channel_name in REVERSED_CHANNELS:
        from shapely.geometry import LineString
        geom = LineString(list(geom.coords)[::-1])
    total = geom.length   # m (UTM)
    print(f"  Channel: {channel_name}  total length: {total:.0f} m")

    frames = []
    n_sheets = math.ceil(total / seg_len)

    for i in range(n_sheets):
        c_start = i * seg_len
        c_end   = min((i + 1) * seg_len, total)
        seg     = substring(geom, c_start, c_end)

        center_pt   = seg.interpolate(0.5, normalized=True)
        bearing_deg = _mean_bearing(seg)
        corners     = _frame_corners(
            (center_pt.x, center_pt.y), bearing_deg, along_m, perp_m
        )

        frames.append(ViewFrame(
            index       = i + 1,
            c_start     = c_start,
            c_end       = c_end,
            center_utm  = (center_pt.x, center_pt.y),
            bearing_deg = bearing_deg,
            along_m     = along_m,
            perp_m      = perp_m,
            corners_utm = corners,
        ))
        print(f"  {frames[-1]}")

    return geom, frames


# ══════════════════════════════════════════════════════════════════════════════
# 4.  Satellite background helpers (reuse W1 tile cache)
# ══════════════════════════════════════════════════════════════════════════════

def _deg2num(lat, lon, z):
    n = 2**z
    xt = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    yt = int((1 - math.log(math.tan(lat_r) + 1/math.cos(lat_r)) / math.pi) / 2 * n)
    return xt, yt


def _num2lonlat(xt, yt, z):
    n = 2**z
    lon = xt / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2*yt/n))))
    return lon, lat


import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

_session_lock = threading.Lock()
_session = None

def _get_session():
    global _session
    if _session is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        with _session_lock:
            if _session is None:
                s = requests.Session()
                retry = Retry(total=3, backoff_factor=0.3,
                              status_forcelist=(500, 502, 503, 504))
                adapter = HTTPAdapter(max_retries=retry,
                                      pool_connections=32, pool_maxsize=32)
                s.mount("https://", adapter)
                s.headers["User-Agent"] = "Mozilla/5.0 PP-Script/2.0"
                _session = s
    return _session


def _fetch_tile(z, x, y):
    from PIL import Image
    import io
    cache = os.path.join(IMG_DIR, "tiles", f"{z}_{x}_{y}.jpg")
    if os.path.exists(cache):
        return np.array(Image.open(cache).convert("RGB"))
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    r = _get_session().get(
        ESRI_TILE_URL.format(z=z, y=y, x=x), timeout=15)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    img.save(cache, "JPEG", quality=90)
    return np.array(img)


def fetch_background(bbox_utm, epsg=32640, target_px=None):
    """
    Download and stitch tiles covering bbox_utm (xmin,ymin,xmax,ymax).
    Returns (img_array, x_min_3857, y_max_3857, px_size_3857)
    so callers can map UTM → pixel for satellite sampling.
    """
    if target_px is None:
        target_px = SAT_TILE_QUALITY

    from pyproj import Transformer
    t4 = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    t3 = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:3857", always_xy=True)

    lon0, lat0 = t4.transform(bbox_utm[0], bbox_utm[1])
    lon1, lat1 = t4.transform(bbox_utm[2], bbox_utm[3])
    lon0, lon1 = min(lon0,lon1), max(lon0,lon1)
    lat0, lat1 = min(lat0,lat1), max(lat0,lat1)

    # Cap at z=18: z=19 tiles are missing for many rural areas in this region
    z = 14
    for zi in range(18, 10, -1):
        xt0, yt0 = _deg2num(lat1, lon0, zi)
        xt1, yt1 = _deg2num(lat0, lon1, zi)
        cols = abs(xt1-xt0)+1; rows = abs(yt1-yt0)+1
        if cols * rows <= 400 and cols * TILE_SIZE >= target_px // 4:
            z = zi; break

    xt0, yt0 = _deg2num(lat1, lon0, z)
    xt1, yt1 = _deg2num(lat0, lon1, z)
    if xt0 > xt1: xt0, xt1 = xt1, xt0
    if yt0 > yt1: yt0, yt1 = yt1, yt0

    cols = xt1-xt0+1; rows = yt1-yt0+1
    canvas = np.zeros((rows*TILE_SIZE, cols*TILE_SIZE, 3), dtype=np.uint8)

    # Build full task list; cached tiles are loaded inline, missing ones fetched in parallel
    tasks = [(iy, ix, ty, tx)
             for iy, ty in enumerate(range(yt0, yt1+1))
             for ix, tx in enumerate(range(xt0, xt1+1))]
    n_total  = len(tasks)
    n_cached = sum(1 for _, _, ty, tx in tasks
                   if os.path.exists(os.path.join(IMG_DIR, "tiles", f"{z}_{tx}_{ty}.jpg")))
    n_fetch  = n_total - n_cached
    print(f"    Satellite: z={z} {cols}x{rows} tiles "
          f"({n_cached} cached, {n_fetch} to download)", end="", flush=True)

    def _place(iy, ix, tile):
        canvas[iy*TILE_SIZE:(iy+1)*TILE_SIZE,
               ix*TILE_SIZE:(ix+1)*TILE_SIZE] = tile

    workers = min(32, n_total)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_fetch_tile, z, tx, ty): (iy, ix)
                for iy, ix, ty, tx in tasks}
        for fut in as_completed(futs):
            iy, ix = futs[fut]
            try:
                _place(iy, ix, fut.result())
            except Exception:
                pass
    print(" done")

    # Image georef in EPSG:3857
    lon_nw, lat_nw = _num2lonlat(xt0,   yt0,   z)
    lon_se, lat_se = _num2lonlat(xt1+1, yt1+1, z)
    t38 = Transformer.from_crs("EPSG:4326","EPSG:3857",always_xy=True)
    x3_nw,y3_nw = t38.transform(lon_nw,lat_nw)
    x3_se,y3_se = t38.transform(lon_se,lat_se)

    total_w = cols*TILE_SIZE; total_h = rows*TILE_SIZE
    px_x = (x3_se-x3_nw)/total_w
    px_y = (y3_se-y3_nw)/total_h   # negative

    return canvas, x3_nw, y3_nw, px_x, px_y, (x3_nw,y3_nw,x3_se,y3_se)


def warp_satellite_to_frame(vf, epsg=32640, out_w=None, out_h=None):
    """
    Download satellite tiles covering vf's bounding box and warp them
    into the view frame's local coordinate system (channel goes left-right).
    Returns a (H, W, 3) uint8 numpy array.
    Uses bilinear interpolation for best quality.
    """
    from pyproj import Transformer

    if out_w is None: out_w = int(vf.along_m * 4)
    if out_h is None: out_h = int(vf.perp_m  * 4)

    # Bounding box in UTM (axis-aligned, covers the rotated frame)
    corners = np.array(vf.corners_utm)
    xmin, ymin = corners[:,0].min()-50, corners[:,1].min()-50
    xmax, ymax = corners[:,0].max()+50, corners[:,1].max()+50
    canvas, x3_nw, y3_nw, px_x, px_y, _ = fetch_background(
        (xmin,ymin,xmax,ymax), epsg=epsg)

    # Transform: UTM → 3857
    t38 = Transformer.from_crs(f"EPSG:{epsg}","EPSG:3857",always_xy=True)

    # Bearing rotation angle (to map channel direction → output +x)
    b   = math.radians(vf.bearing_deg)
    # standard math angle of channel direction: atan2(sin_b_east, cos_b_north)
    # channel unit vector in (UTM_x, UTM_y): (sin b, cos b)
    # math angle = atan2(cos b, sin b)? No:
    # UTM_x = east, UTM_y = north
    # channel goes: dx=sin(b), dy=cos(b)
    # math angle from +x axis: atan2(cos b, sin b)... let me be explicit
    # rotation to map channel to +output_x: theta = -atan2(channel_dy, channel_dx)
    ch_dx = math.sin(b); ch_dy = math.cos(b)
    theta = -math.atan2(ch_dy, ch_dx)
    cos_t = math.cos(theta); sin_t = math.sin(theta)

    cx, cy = vf.center_utm
    along_half = vf.along_m / 2
    perp_half  = vf.perp_m  / 2

    # For each output pixel, compute UTM coords → 3857 → canvas index
    # Output grid in local frame: s in [-along_half, along_half], n in [perp_half, -perp_half]
    s_lin = np.linspace(-along_half, along_half, out_w)
    n_lin = np.linspace( perp_half, -perp_half,  out_h)  # top row = +n
    ss, nn = np.meshgrid(s_lin, n_lin)

    # Local (s,n) → UTM: inverse of rotation
    # utm_dx = ss*cos(-theta) - nn*sin(-theta) = ss*cos_t + nn*sin_t
    # utm_dy = ss*sin(-theta) + nn*cos(-theta) = -ss*sin_t + nn*cos_t
    utm_x = cx + ss*cos_t + nn*sin_t
    utm_y = cy - ss*sin_t + nn*cos_t

    # UTM → 3857
    x3_arr, y3_arr = t38.transform(utm_x.ravel(), utm_y.ravel())
    x3_arr = x3_arr.reshape(out_h, out_w)
    y3_arr = y3_arr.reshape(out_h, out_w)

    # 3857 → pixel index in canvas
    col_f = (x3_arr - x3_nw) / px_x
    row_f = (y3_arr - y3_nw) / px_y   # px_y is negative

    # Bilinear interpolation (numpy-only, no scipy dependency)
    ch, cw = canvas.shape[:2]
    row_lo = np.clip(np.floor(row_f).astype(int), 0, ch - 1)
    row_hi = np.clip(row_lo + 1,                  0, ch - 1)
    col_lo = np.clip(np.floor(col_f).astype(int), 0, cw - 1)
    col_hi = np.clip(col_lo + 1,                  0, cw - 1)
    wr = (row_f - np.floor(row_f))[:, :, np.newaxis]
    wc = (col_f - np.floor(col_f))[:, :, np.newaxis]
    warped = (
        (1 - wr) * (1 - wc) * canvas[row_lo, col_lo] +
        (1 - wr) * wc       * canvas[row_lo, col_hi] +
        wr       * (1 - wc) * canvas[row_hi, col_lo] +
        wr       * wc       * canvas[row_hi, col_hi]
    ).clip(0, 255).astype(np.uint8)

    return warped


# ══════════════════════════════════════════════════════════════════════════════
# 5.  View-frame diagram (matplotlib PDF)
# ══════════════════════════════════════════════════════════════════════════════

def _frame_corners_exact(center_xy, bearing_rad, seg_len, perp_m):
    """
    Corners for DIAGRAM ONLY — exactly seg_len along, no margin overlap.
    Left edge = sta start,  Right edge = sta end.
    """
    s_vec = np.array([math.sin(bearing_rad),  math.cos(bearing_rad)])
    n_vec = np.array([-math.cos(bearing_rad), math.sin(bearing_rad)])
    c = np.array(center_xy)
    half_s = seg_len / 2
    half_n = perp_m  / 2
    return [
        tuple(c - half_s*s_vec - half_n*n_vec),  # start / right side
        tuple(c + half_s*s_vec - half_n*n_vec),  # end   / right side
        tuple(c + half_s*s_vec + half_n*n_vec),  # end   / left  side
        tuple(c - half_s*s_vec + half_n*n_vec),  # start / left  side
    ]


def make_vf_diagram(channel_name, geom, frames, epsg=32640):
    """
    Produce a clear PDF overview of all view frames.

    Key design choices:
    - Frame boxes drawn at EXACT 1000m (no 50m margin) so adjacent frames
      share edges cleanly and the sta labels sit at the frame edges.
    - Station labels placed at the LEFT (start) edge of each frame.
    - Figure aspect ratio matches channel shape (tall for N-S channel).
    - Satellite background.
    """
    # ── Satellite background ──────────────────────────────────────────────
    bounds = geom.bounds
    buf = 600
    bbox = (bounds[0]-buf, bounds[1]-buf, bounds[2]+buf, bounds[3]+buf)
    print("  Fetching overview satellite...")
    canvas, x3_nw, y3_nw, px_x, px_y, _ = fetch_background(
        bbox, epsg=epsg, target_px=1024)

    t_back = Transformer.from_crs("EPSG:3857", f"EPSG:{epsg}", always_xy=True)
    xm_nw, ym_nw = t_back.transform(x3_nw, y3_nw)
    xm_se, ym_se = t_back.transform(
        x3_nw + px_x * canvas.shape[1],
        y3_nw + px_y * canvas.shape[0])

    # ── Figure size — proportional to channel extent ──────────────────────
    ch_w = bounds[2] - bounds[0]   # E-W span (m)
    ch_h = bounds[3] - bounds[1]   # N-S span (m)
    fig_w = 10.0                   # inches
    fig_h = max(8.0, fig_w * (ch_h / ch_w))  # maintain aspect
    fig_h = min(fig_h, 40.0)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor="white")
    ax.imshow(canvas,
              extent=[xm_nw, xm_se, ym_se, ym_nw],
              origin="upper", interpolation="bilinear", zorder=0)
    ax.set_aspect("equal")

    # ── Other channels (light grey) ───────────────────────────────────────
    gdf = gpd.read_file(SHP_PATH)
    for _, row in gdf.iterrows():
        if row["Name"].lower() == channel_name.lower():
            continue
        g = row.geometry
        if g and g.geom_type == "LineString":
            ax.plot(*g.xy, color="#CCCCCC", lw=0.6, zorder=1)

    # ── Main channel (white outline + red fill for visibility) ───────────
    xs, ys = geom.xy
    ax.plot(xs, ys, color="white",  lw=3.5, zorder=2)
    ax.plot(xs, ys, color="#EE2200", lw=1.8, zorder=3, label=channel_name)

    # ── Station ticks every 1000m along channel ───────────────────────────
    for vf in frames:
        # Mark the exact start point of this segment on the channel
        p_sta = geom.interpolate(vf.c_start)
        ax.plot(p_sta.x, p_sta.y, 'wo', ms=4, zorder=6)
        ax.plot(p_sta.x, p_sta.y, 'o', color="#EE2200", ms=2.5, zorder=7)

    # ── View frame boxes (exact 1000m, no overlap) ───────────────────────
    cmap = plt.cm.tab20
    for vf in frames:
        b_rad = math.radians(vf.bearing_deg)
        corners = _frame_corners_exact(
            vf.center_utm, b_rad, SEGMENT_LEN, vf.perp_m)
        c4 = corners + [corners[0]]
        xs_f = [c[0] for c in c4]
        ys_f = [c[1] for c in c4]
        color = cmap((vf.index - 1) / max(len(frames), 1))

        ax.fill(xs_f[:-1], ys_f[:-1], color=color, alpha=0.25, zorder=4)
        ax.plot(xs_f, ys_f, color=color, lw=1.2, zorder=5)

        # ── Labels at the START edge of each frame ────────────────────────
        # Start edge centre = corner[0] midpoint to corner[3]
        start_mid_x = (corners[0][0] + corners[3][0]) / 2
        start_mid_y = (corners[0][1] + corners[3][1]) / 2

        # Perpendicular shift outward from start edge (away from frame)
        s_vec = np.array([math.sin(b_rad), math.cos(b_rad)])
        lbl_x = start_mid_x - s_vec[0] * 30
        lbl_y = start_mid_y - s_vec[1] * 30

        ax.text(lbl_x, lbl_y,
                f"#{vf.index}  {vf.sta_start}",
                color=color, fontsize=6.5, fontweight="bold",
                ha="center", va="center", zorder=8,
                bbox=dict(boxstyle="round,pad=0.15", fc="white",
                          alpha=0.85, ec=color, lw=0.8))

    # ── Final formatting ──────────────────────────────────────────────────
    ax.set_title(
        f"View Frames — {channel_name}\n"
        f"{len(frames)} sheets  |  {int(SEGMENT_LEN)} m per sheet  |  "
        f"plan {int(PLAN_ALONG_M)}m × {int(PLAN_PERP_M)}m",
        fontsize=10, pad=8)
    ax.set_xlabel("UTM Easting (m)", fontsize=8)
    ax.set_ylabel("UTM Northing (m)", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.8)
    plt.tight_layout(pad=1.5)

    out = os.path.join(OUT_VF, f"{channel_name}_view_frames.pdf")
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"  Diagram saved: {out}")
    return out, frames


# ══════════════════════════════════════════════════════════════════════════════
# 6.  Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Ais-CH1-FP"
    print(f"\nComputing view frames for {name}...")
    geom, frames = compute_view_frames(name)
    make_vf_diagram(name, geom, frames)
    print(f"\n{len(frames)} view frames defined.")
