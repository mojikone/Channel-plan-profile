# Channel Plan & Profile Generator

Automated pipeline that produces A1 plan+profile DXF sheets and PDF deliverables for hydraulic channel alignments, combining SewerGEMS profile data with GIS shapefiles and ESRI World Imagery satellite tiles.

---

## Folder Structure

```
repo-root/
├── config.py              # All paths, viewport constants, layer colours
├── run_all.py             # Entry point — generates all sheets in parallel
├── make_sheet.py          # Builds one plan+profile A1 sheet DXF
├── make_keymap.py         # Builds the key-map overview sheet DXF
├── export_pdf.py          # Exports DXFs to compact vector+satellite PDFs
├── view_frames.py         # Computes sheet view frames; downloads satellite tiles
├── make_vf_dxf.py         # (optional) Exports view frames as UTM DXF for review
│
├── Data/                  # ← INPUT — place your project data here
│   ├── DXF/               # SewerGEMS profile DXF files — one per channel (e.g. Ais-CH1-FP.dxf)
│   ├── SHP/
│   │   ├── Channels.shp           # Channel alignment shapefile
│   │   ├── Buffers/
│   │   │   ├── Channel Bed Buffer.shp
│   │   │   └── Channels Buffer.shp
│   │   └── Landuse/
│   │       └── LandUse.shp
│   └── Template/
│       └── Plan Profile.dxf       # A1 title-block template DXF
│
├── DXF/                   # ← OUTPUT — generated plan+profile DXFs + satellite images (.jpg)
├── PDF/                   # ← OUTPUT — individual sheet PDFs
│   └── Combined/          # ← OUTPUT — one combined PDF per channel
├── IMG/
│   └── tiles/             # Satellite tile cache (auto-populated on first run)
└── view_frames/           # ← OUTPUT — view-frame overview PNGs (optional debug)
```

---

## Input Files

### 1. SewerGEMS Profile DXFs — `Data/DXF/<ChannelName>.dxf`
One DXF per channel, exported directly from SewerGEMS.  
The file name must exactly match the `Name` field in the channel shapefile (e.g. `Ais-CH1-FP.dxf`).

Required layers inside each DXF:

| Layer | Content |
|---|---|
| `Crown` | Channel crown (top) profile line |
| `Invert` | Channel invert (bottom) profile line |
| `Hydraulic_Grade` | HGL / water surface profile |
| `Energy_Grade` | EGL profile |
| `Ground_Elevation` | Natural ground line |
| `Grid` | Vertical and horizontal grid lines |
| `Grid_Text` | Station labels along the x-axis (format `0+050`) |
| `Annotation_Table` | Data band (table rows: Label, Length, Size, Flow, Slope, Elevations, Station) |
| `Annotation` | In-chart conduit annotations |
| `Structure` | Manhole / outfall symbols |

The annotation band can be at negative y (standard SewerGEMS layout) or positive y (non-standard). The pipeline detects and handles both automatically.

---

### 2. Channel Alignment Shapefile — `Data/SHP/Channels.shp`
A single polyline shapefile containing all channel alignments.

Required attribute field:

| Field | Type | Description |
|---|---|---|
| `Name` | String | Channel name — must match the profile DXF filename (without `.dxf`) |

Coordinate system: **UTM Zone 40N (EPSG:32640)**.

---

### 3. Buffer Shapefiles — `Data/SHP/Buffers/`
Two polygon shapefiles used as overlay outlines on the plan view:

| File | Description |
|---|---|
| `Channel Bed Buffer.shp` | Channel bed buffer polygon |
| `Channels Buffer.shp` | Channel corridor buffer polygon |

Coordinate system: EPSG:32640. If the bed buffer has no CRS, it is assigned EPSG:32640 automatically.

---

### 4. Land Use Shapefile — `Data/SHP/Landuse/LandUse.shp` *(optional)*
Polygon shapefile of land use areas, drawn as thin grey outlines on the plan view.  
Currently disabled by default (`gdf_lu = None`). To enable, load it in `run_all.py → load_overlays()`.

---

### 5. Title-Block Template DXF — `Data/Template/Plan Profile.dxf`
An A1 DXF file containing the standard title block with ATTRIB tags.

Required ATTRIB tags the pipeline writes to:

| Tag | Content written |
|---|---|
| `DRAWING-TITLE1` | `Plan & Profile Of <ChannelName>  <Sta> - <Sta>  Sheet #N of M` |
| `DOC_NO1` | Sequential drawing number, e.g. `2224-PD-HY-PP-B6-001` |
| `SCALE` | Plan horizontal scale, e.g. `1:1250` |
| `DATE` | Generation date (`DD.MM.YYYY`) |
| `PROJECT-TITLE3` | Project block identifier (e.g. `Block 06`) |
| `REV_NO` / `REV_NO1` | Revision number |

---

## Configuration — `config.py`

Key constants to adjust for a new project:

| Constant | Description |
|---|---|
| `SEGMENT_LEN` | Chainage length per sheet (default: 1000 m) |
| `PLAN_MARGIN_M` | Context overlap shown before/after each segment (default: 100 m) |
| `CATCHMENT_ORDER` | Ordered list of catchment prefixes for drawing numbering |
| `DOC_NO_PREFIX` | Drawing number prefix (e.g. `2224-PD-HY-PP-B6`) |
| `REVERSED_CHANNELS` | Set of channel names whose shapefile direction is reversed vs the DXF |
| `PROF_TEXT_H` | Text heights (mm) per layer in the profile |
| `LAYER_COLORS` | ACI colour per profile layer |

---

## Workflow

```
SewerGEMS DXF  +  Channel SHP  +  Buffers SHP  →  run_all.py
                                                        │
                                        ┌───────────────┴──────────────┐
                                        │  parallel workers (all cores) │
                                        │  make_sheet.py × N sheets     │
                                        │  make_keymap.py × 1 key map   │
                                        └───────────────┬──────────────┘
                                                        │
                                                   DXF/  (sheets + key maps)
                                                        │
                                               export_pdf.py
                                                        │
                                         ┌──────────────┴──────────────┐
                                         │                              │
                                      PDF/                    PDF/Combined/
                                  (per sheet)              (one PDF per channel)
```

### Per-sheet process (`make_sheet.py`)
1. Compute view frame (bearing-aligned rectangle, 1000 m along channel)
2. Download/cache ESRI World Imagery tiles → warp to frame bearing
3. Scan profile DXF → determine elevation window for this chainage range
4. Copy profile entities (polylines, text, grid) into paper-space with coordinate transform
5. Draw plan view: satellite image, buffer overlays, channel alignment, station ticks
6. Write title block attributes (drawing number, scale, date)

### PDF export (`export_pdf.py`)
- Vector content rendered by ezdxf + matplotlib → vector PDF layer
- Satellite image embedded directly as JPEG → satellite PDF layer
- Two layers merged with pikepdf → compact combined PDF (~1–2 MB per A1 sheet)
- All sheets for a channel merged into one combined PDF

---

## Usage

```powershell
# Generate all channels (parallel, all CPU cores)
python run_all.py

# Generate one channel
python run_all.py Ais-CH1-FP

# Generate specific sheets of one channel
python run_all.py Ais-CH1-FP 3 5

# Export all DXFs to PDF and build combined files
python export_pdf.py

# Rebuild combined PDFs only (without re-exporting sheets)
python export_pdf.py --rebuild-combined
```

All outputs go to `DXF/` and `PDF/`. Satellite tiles are cached in `IMG/tiles/` — first run downloads tiles; subsequent runs use the cache.

---

## Dependencies

```
ezdxf
geopandas
shapely
numpy
Pillow
pyproj
matplotlib
requests
reportlab
pikepdf
```

Install with:
```bash
pip install ezdxf geopandas shapely numpy Pillow pyproj matplotlib requests reportlab pikepdf
```
