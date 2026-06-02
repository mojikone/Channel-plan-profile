"""
Generate all plan-profile sheets + key maps for all channels.

Usage:
    python scripts\run_all.py                        # all channels, all cores
    python scripts\run_all.py Ais-CH1-FP             # one channel
    python scripts\run_all.py Ais-CH1-FP 3 5         # sheets 3-5 only
"""
import os, sys, re, time
sys.path.insert(0, os.path.dirname(__file__))

from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing


# ── Worker (runs in a child process) ─────────────────────────────────────────

def _worker(task):
    """Process one sheet or key-map.  Loads overlays independently per worker."""
    channel, idx, doc_no = task   # idx = int (sheet) or 'km' (key map)
    try:
        import sys, os, warnings
        warnings.filterwarnings("ignore")
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        from config import SHP_BUF_BED, SHP_BUF_CH
        import geopandas as gpd

        gdf_bed = gpd.read_file(SHP_BUF_BED)
        if gdf_bed.crs is None:
            gdf_bed = gdf_bed.set_crs("EPSG:32640")
        gdf_ch  = gpd.read_file(SHP_BUF_CH)
        gdf_bufs = [("BUF_BED", gdf_bed), ("BUF_CH", gdf_ch)]

        if idx == "km":
            from make_keymap import make_keymap
            make_keymap(channel, gdf_lu=None, gdf_bufs=gdf_bufs, doc_no=doc_no)
        else:
            from make_sheet import make_sheet
            make_sheet(channel, idx, gdf_lu=None, gdf_bufs=gdf_bufs, doc_no=doc_no)

        label = "KeyMap" if idx == "km" else f"Sheet{idx:02d}"
        return (channel, label, None)

    except Exception as e:
        import traceback
        label = "KeyMap" if idx == "km" else f"Sheet{idx:02d}"
        return (channel, label, traceback.format_exc())


# ── Helpers ───────────────────────────────────────────────────────────────────

def available_channels():
    """Return channel names that have both a shapefile entry and a profile DXF."""
    import geopandas as gpd
    from config import SHP_PATH, DXF_DIR
    gdf = gpd.read_file(SHP_PATH)
    dxf_names = {
        os.path.splitext(f)[0].lower()
        for f in os.listdir(DXF_DIR) if f.lower().endswith(".dxf")
    }
    channels, skipped = [], []
    for name in sorted(gdf["Name"].unique()):
        (channels if name.lower() in dxf_names else skipped).append(name)
    if skipped:
        print(f"  Skipped (no profile DXF): {', '.join(skipped)}")
    return channels


def _channel_sort_key(name):
    """Sort key: by CATCHMENT_ORDER index, then by CH number."""
    from config import CATCHMENT_ORDER
    for i, cat in enumerate(CATCHMENT_ORDER):
        if name.upper().startswith(cat.upper()):
            m = re.search(r'CH(\d+)', name, re.IGNORECASE)
            return (i, int(m.group(1)) if m else 999)
    return (999, 999)


def assign_drawing_numbers(channels):
    """
    Pre-compute global sequential drawing numbers for every sheet and key map.
    Order: catchment order → CH number → KeyMap first, then Sheet01, Sheet02…
    Returns dict: (channel, idx_or_'km') -> doc_no string
    """
    from config import DOC_NO_PREFIX
    from view_frames import compute_view_frames

    sorted_ch = sorted(channels, key=_channel_sort_key)
    numbers   = {}
    counter   = 1
    for ch in sorted_ch:
        _, frames = compute_view_frames(ch)
        numbers[(ch, "km")] = f"{DOC_NO_PREFIX}-{counter:03d}"
        counter += 1
        for i in range(1, len(frames) + 1):
            numbers[(ch, i)] = f"{DOC_NO_PREFIX}-{counter:03d}"
            counter += 1
    return numbers


def build_tasks(channels, doc_numbers, s_from=None, s_to=None):
    """Build list of (channel, idx, doc_no) tasks."""
    from view_frames import compute_view_frames
    tasks = []
    for ch in channels:
        _, frames = compute_view_frames(ch)
        n  = len(frames)
        lo = s_from if s_from is not None else 1
        hi = s_to   if s_to   is not None else n
        for idx in range(lo, hi + 1):
            tasks.append((ch, idx, doc_numbers.get((ch, idx), "")))
        if s_from is None:
            tasks.append((ch, "km", doc_numbers.get((ch, "km"), "")))
    return tasks


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Parse CLI
    if len(sys.argv) > 1 and not sys.argv[1].isdigit():
        channels = [sys.argv[1]]
        s_from = int(sys.argv[2]) if len(sys.argv) >= 3 else None
        s_to   = int(sys.argv[3]) if len(sys.argv) >= 4 else s_from
    else:
        print("\nDiscovering channels with profile DXFs...")
        channels = available_channels()
        s_from = s_to = None
        print(f"{len(channels)} channels found.\n")

    print("Assigning drawing numbers...")
    # Always compute numbers over ALL channels so numbers are globally consistent
    all_channels = available_channels() if len(channels) > 1 else None
    doc_numbers  = assign_drawing_numbers(all_channels or channels)

    print("Building task list...")
    tasks   = build_tasks(channels, doc_numbers, s_from, s_to)
    n_tasks = len(tasks)

    workers = min(multiprocessing.cpu_count(), 12)
    print(f"Running {n_tasks} tasks on {workers} parallel workers...\n")

    t0      = time.time()
    done    = 0
    errors  = []

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_worker, t): t for t in tasks}
        for fut in as_completed(futs):
            done += 1
            channel, label, err = fut.result()
            if err:
                errors.append((channel, label, err))
                print(f"  [ERR] {channel} {label}", flush=True)
            else:
                print(f"  [OK]  {channel} {label}  ({done}/{n_tasks})",
                      flush=True)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for ch, lbl, tb in errors:
            print(f"  {ch} {lbl}:\n{tb}")
    print(f"All done in {elapsed:.0f}s  "
          f"({n_tasks - len(errors)}/{n_tasks} OK, "
          f"{len(channels)} channels)")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
