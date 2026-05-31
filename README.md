# Channel Plan-Profile DXF Generator

Python tools for producing A1 plan-profile DXF sheets from SewerGEMS channel
profiles and GIS alignments.

## Features

- Generates plan sheets with satellite imagery, alignment station ticks, labels,
  north arrow, and scale bar.
- Copies SewerGEMS profile traces for ground, hydraulic grade, crown, and invert.
- Draws profile grids and the annotation table.
- Uses Times New Roman for generated annotations.
- Keeps profile vertical exaggeration at 10:1.
- Writes versioned DXF and satellite-image outputs without overwriting earlier
  exports.

## Main Scripts

- `scripts/make_sheet.py`: generates an A1 plan-profile sheet.
- `scripts/view_frames.py`: computes sheet frames and satellite backgrounds.
- `scripts/make_vf_dxf.py`: exports view frames for alignment review.
- `scripts/config.py`: paths, viewport geometry, colors, and text sizes.

## Usage

Install the required Python packages, including `ezdxf`, `geopandas`, `shapely`,
`numpy`, `Pillow`, `pyproj`, `matplotlib`, and `requests`.

Generate the first sheet:

```powershell
python scripts\make_sheet.py Ais-CH1-FP 1
```

The generated DXF and its satellite PNG are written to `DXF/`.

## Local Data

The scripts expect the source SewerGEMS DXF, shapefile, and title-block template
to exist in the configured project data folders. Generated DXFs, imagery, PDF
exports, and tile caches are ignored by Git.
