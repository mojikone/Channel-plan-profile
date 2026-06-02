"""
Channel plan-profile sheet generator — configuration.
All measurements in mm (paper space) unless noted.

All paths are relative to the repository root (the folder containing this file).
"""
import os, datetime

# Repository root = folder containing this script
ROOT     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "Data")

# ── Input paths ───────────────────────────────────────────────────────────────
DXF_DIR  = os.path.join(DATA_DIR, "DXF")                              # SewerGEMS profile DXFs
SHP_PATH = os.path.join(DATA_DIR, "SHP", "Channels.shp")             # channel alignment shapefile
TMPL     = os.path.join(DATA_DIR, "Template", "Plan Profile.dxf")    # A1 title-block template

# ── Output paths ──────────────────────────────────────────────────────────────
IMG_DIR  = os.path.join(ROOT, "IMG")                 # satellite tile cache
OUT_VF   = os.path.join(ROOT, "view_frames")         # view-frame debug outputs
OUT_DXF  = os.path.join(ROOT, "DXF")                # generated plan+profile DXFs
OUT_PDF  = os.path.join(ROOT, "PDF")                # generated PDFs

# ── Template paper-space layout (DXF absolute coords) ─────────────────────────
# Paper A1 landscape 841×594 mm, origin shifted to (974.55, -17.15)
PAPER_ORIG_X = 974.55
PAPER_ORIG_Y = -17.15
PAPER_W = 841.0
PAPER_H = 594.0

# Plan viewport (absolute paper-space DXF coords)
# Plan height increased ×1.3 vs original template; profile shrinks to compensate.
VP_PLAN_X0  = 1003.55
VP_PLAN_X1  = 1796.55
VP_PLAN_Y1  = 557.5
VP_PLAN_Y0  = 245.57          # = Y1 - (original_H × 1.3)  → plan H ≈ 312 mm
VP_PLAN_W   = VP_PLAN_X1 - VP_PLAN_X0   # 793 mm
VP_PLAN_H   = VP_PLAN_Y1 - VP_PLAN_Y0   # ≈ 312 mm

# Profile viewport (absolute paper-space DXF coords)
VP_PROF_X0  = 1003.55
VP_PROF_X1  = 1796.55
VP_PROF_Y0  = 47.0
VP_PROF_Y1  = VP_PLAN_Y0 - 8.0   # 8 mm gap below plan  → prof Y1 ≈ 237.57
VP_PROF_W   = VP_PROF_X1 - VP_PROF_X0   # 793 mm
VP_PROF_H   = VP_PROF_Y1 - VP_PROF_Y0   # ≈ 190.6 mm

# ── Profile DXF scaling (SewerGEMS Ais-CH1-FP global extents) ─────────────────
# x: 1 DXF unit = 1 m chainage
# y: 10 DXF units = 1 m elevation  (1:10 vertical)
# annotation band: y = -140.2 .. 0  DXF
# profile chart  : y =    0   .. 760 DXF  (0–76 m elevation)
PROF_Y_MIN    = -140.2      # bottom of annotation band (DXF y)
PROF_Y_MAX    =  760.0      # top grid line (DXF y)
PROF_Y_RANGE  = PROF_Y_MAX - PROF_Y_MIN   # 900.2 DXF units

PROF_SCALE_X  = VP_PROF_W / 1000.0                     # mm per DXF-x unit (per 1000m segment)
PROF_SCALE_Y  = VP_PROF_H / PROF_Y_RANGE               # mm per DXF-y unit (uniform)
# → H scale ~1:1261, V scale ~1:350, VE ~3.6:1

SEGMENT_LEN   = 1000.0      # m of chainage per sheet

# ── Plan view frame sizing ─────────────────────────────────────────────────────
# Each sheet shows 100 m before its start station and 100 m after its end station
# → first sheet shows 100 m "empty" before 0+000
# → every sheet overlaps 100 m with the adjacent frame on each side
# → last sheet shows 100 m after end of alignment
PLAN_MARGIN_M = 100.0       # m of context shown before start and after end
PLAN_ALONG_M  = SEGMENT_LEN + 2 * PLAN_MARGIN_M  # 1200 m total along-channel
# Perpendicular extent derived from viewport aspect ratio (equal-scale plan)
PLAN_PERP_M   = PLAN_ALONG_M * (VP_PLAN_H / VP_PLAN_W)   # ~371 m total (±185 m)

# ── SewerGEMS profile layers → display colours ────────────────────────────────
# Green = Ground,  Blue = HGL (water level),  White/Black = Crown & Invert
LAYER_COLORS = {
    "Crown":           7,    # white/black – channel top
    "Invert":          7,    # white/black – channel bottom
    "Hydraulic_Grade": 5,    # blue        – water level / HGL
    "Energy_Grade":    1,    # red         – EGL
    "Ground_Elevation":3,    # green       – natural ground
    "Grid":            253,  # light grey
    "Grid_Text":       7,    # black
    "Annotation":      7,    # black
    "Annotation_Table":7,    # black
    "Structure":       6,    # magenta – manholes / outfalls
}

# ── Fixed text heights on paper (mm) for profile entities ─────────────────────
PROF_TEXT_H = {
    "Grid_Text":        4.0,   # elevation axis + station labels  (×2 from before)
    "Annotation":       4.5,   # conduit & structure annotations
    "Annotation_Table": 4.2,   # data-band table cells
    "Structure":        3.5,   # manhole labels
}

# ── Profile x-margin (m each end) ─────────────────────────────────────────────
# Leaves 25 m of "breathing space" so text at sta 0 and 1+000 is not clipped
PROF_X_MARGIN = 25.0    # m

# ── Channels whose shapefile digitisation is opposite to the profile DXF ──────
REVERSED_CHANNELS = {"Taw-CH5-SM"}

# ── Drawing set order ─────────────────────────────────────────────────────────
# Catchments processed in this order; within each, channels by CH number.
CATCHMENT_ORDER = ["Ais", "Rus", "Ban", "Maa", "Taw", "Ful"]
DOC_NO_PREFIX   = "2224-PD-HY-PP-B6"

# ── Overlay shapefiles ────────────────────────────────────────────────────────
SHP_BUF_BED = os.path.join(DATA_DIR, "SHP", "Buffers", "Channel Bed Buffer.shp")
SHP_BUF_CH  = os.path.join(DATA_DIR, "SHP", "Buffers", "Channels Buffer.shp")
SHP_LANDUSE = os.path.join(DATA_DIR, "SHP", "Landuse", "LandUse.shp")

# Brown true-colour (24-bit RGB) for buffer outlines; global width in paper mm
BUFFER_TRUE_COLOR = (139, 69, 19)   # saddle brown
BUFFER_GLOBAL_W   = 1.0             # mm paper-space const_width
BUFFER_LINEWEIGHT = 100             # 1.00 mm fallback lineweight

# Light-grey ACI colour for land-use outlines (thinnest line)
LANDUSE_ACI_COLOR = 253
LANDUSE_LINEWEIGHT = 0              # thinnest

# ── ESRI tile cache ────────────────────────────────────────────────────────────
ESRI_TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TILE_SIZE = 256
SAT_TILE_QUALITY = 4096   # target pixel count for zoom-level selection
