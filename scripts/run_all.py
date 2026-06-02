"""
Generate all plan-profile sheets + key maps for all channels that have
a profile DXF in Data/DXF/.

Usage:
    python scripts\run_all.py                        # all channels
    python scripts\run_all.py Ais-CH1-FP             # one channel
    python scripts\run_all.py Ais-CH1-FP 3 5         # sheets 3-5 only
"""
import os, sys, math, time, glob
sys.path.insert(0, os.path.dirname(__file__))

import geopandas as gpd

from config import SHP_PATH, DXF_DIR, SHP_BUF_BED, SHP_BUF_CH
from view_frames import compute_view_frames
from make_sheet  import make_sheet
from make_keymap import make_keymap


def load_overlays():
    print("Loading overlay shapefiles...")
    gdf_bed = gpd.read_file(SHP_BUF_BED)
    if gdf_bed.crs is None:
        gdf_bed = gdf_bed.set_crs("EPSG:32640")
    gdf_ch = gpd.read_file(SHP_BUF_CH)
    gdf_bufs = [("BUF_BED", gdf_bed), ("BUF_CH", gdf_ch)]
    return None, gdf_bufs   # land-use disabled


def available_channels():
    """Return list of channel names that have both a shapefile row and a profile DXF."""
    gdf = gpd.read_file(SHP_PATH)
    dxf_lower = {
        os.path.splitext(f)[0].lower(): os.path.splitext(f)[0]
        for f in os.listdir(DXF_DIR) if f.lower().endswith(".dxf")
    }
    channels = []
    for name in sorted(gdf["Name"].unique()):
        if name.lower() in dxf_lower:
            channels.append(name)
        else:
            print(f"  [SKIP] {name} — no profile DXF found")
    return channels


def process_channel(channel, gdf_lu, gdf_bufs, s_from=None, s_to=None):
    print(f"\n{'='*60}")
    print(f"  Channel: {channel}")
    print(f"{'='*60}")

    _, frames = compute_view_frames(channel)
    n = len(frames)
    lo = s_from if s_from is not None else 1
    hi = s_to   if s_to   is not None else n

    for idx in range(lo, hi + 1):
        make_sheet(channel, idx, gdf_lu=gdf_lu, gdf_bufs=gdf_bufs)

    make_keymap(channel, gdf_lu=gdf_lu, gdf_bufs=gdf_bufs)


def main():
    gdf_lu, gdf_bufs = load_overlays()

    # Parse CLI args
    if len(sys.argv) > 1 and not sys.argv[1].isdigit():
        # Specific channel
        channels = [sys.argv[1]]
        s_from = int(sys.argv[2]) if len(sys.argv) >= 3 else None
        s_to   = int(sys.argv[3]) if len(sys.argv) >= 4 else s_from
    else:
        # All channels
        print("\nDiscovering channels with profile DXFs...")
        channels = available_channels()
        s_from = s_to = None
        print(f"\n{len(channels)} channels to process.\n")

    t0 = time.time()
    for ch in channels:
        try:
            process_channel(ch, gdf_lu, gdf_bufs, s_from, s_to)
        except Exception as e:
            print(f"\n[ERROR] {ch}: {e}")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"All done in {elapsed:.0f}s  ({len(channels)} channels)")


if __name__ == "__main__":
    main()
