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

## Required Inputs

Update the paths in `scripts/config.py` to match the local project folders.
The generator requires:

- A SewerGEMS profile DXF named `<channel-name>.dxf`, such as
  `Ais-CH1-FP.dxf`. It must contain the profile grid, annotation table, ground
  elevation, hydraulic grade, crown, invert, structures, and annotations.
- A channel alignment shapefile, such as `Channels Block 06.shp`. Its attribute
  table must include a `Name` field matching the requested channel name.
- An A1 plan-profile DXF template, such as `Plan Profile.dxf`, containing the
  sheet border and title block.
- Internet access to Esri World Imagery for the initial satellite-tile
  download. Downloaded tiles are cached locally in `IMG/tiles/`.

The configured source data is read from the project folders and is not modified.

## Generated Outputs

Running `scripts/make_sheet.py` creates versioned files in `DXF/`:

- `<channel-name>-Sheet<sheet-number>_v<version>.dxf`: the A1 plan-profile
  drawing.
- `<channel-name>_S<sheet-number>_sat_v<version>.png`: the satellite image
  referenced by the generated DXF.

The sheet DXF includes the plan alignment, station ticks and labels, satellite
background, north arrow, scale bar, profile traces, profile grid, annotation
table, and updated title-block text.

Optional review outputs can also be generated:

- `scripts/view_frames.py` creates a PDF overview of the sheet frames.
- `scripts/make_vf_dxf.py` creates a DXF overview of the sheet frames in UTM
  coordinates.

Generate the first sheet:

```powershell
python scripts\make_sheet.py Ais-CH1-FP 1
```

The generated DXF and its satellite PNG are written to `DXF/`.

## Local Data

Generated DXFs, imagery, PDF exports, and tile caches are ignored by Git.
