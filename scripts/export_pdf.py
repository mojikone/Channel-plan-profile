"""
Export DXF files to compact vector PDFs.

Strategy (gives ~1-2 MB per A1 sheet vs 8+ MB from naive rasterization):
  1. Render DXF without the satellite IMAGE entity → pure vector PDF (matplotlib)
  2. Create a satellite-only PDF page with the JPEG embedded directly (reportlab)
  3. Merge: satellite underneath, vectors on top (pikepdf)

All other content (lines, text, borders, profile) is fully vector.
"""
import os, sys, glob, time, warnings, tempfile
sys.path.insert(0, os.path.dirname(__file__))
warnings.filterwarnings("ignore")

import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.units import mm as MM
import pikepdf

from config import (
    PAPER_ORIG_X, PAPER_ORIG_Y, PAPER_W, PAPER_H,
    VP_PLAN_X0, VP_PLAN_Y0, VP_PLAN_W, VP_PLAN_H,
    OUT_DXF, OUT_PDF,
)
# Key-map viewport (full content area)
from make_keymap import KM_VP_X0, KM_VP_Y0, KM_VP_W, KM_VP_H

DXF_DIR      = OUT_DXF
PDF_DIR      = OUT_PDF
PDF_COMBINED = os.path.join(PDF_DIR, "Combined")
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(PDF_COMBINED, exist_ok=True)

# A1 landscape: exactly 841 × 594 mm
MM_TO_IN  = 1.0 / 25.4
FIG_W     = PAPER_W * MM_TO_IN
FIG_H     = PAPER_H * MM_TO_IN
PAPER_X1  = PAPER_ORIG_X + PAPER_W
PAPER_Y1  = PAPER_ORIG_Y + PAPER_H

# Font substitutions for fonts matplotlib cannot find
FONT_SUBS = {
    "sanssb__.ttf": "arial.ttf",
    "sanserif.ttf": "arial.ttf",
    "simplex.shx":  "arial.ttf",
    "simplex":      "arial.ttf",
}


def _is_keymap(dxf_path):
    return "KeyMap" in os.path.basename(dxf_path)


def _sat_viewport(dxf_path):
    """Return (x_mm, y_mm, w_mm, h_mm) of satellite image in A1 paper coords."""
    if _is_keymap(dxf_path):
        x = KM_VP_X0 - PAPER_ORIG_X
        y = KM_VP_Y0 - PAPER_ORIG_Y
        return x, y, KM_VP_W, KM_VP_H
    else:
        x = VP_PLAN_X0 - PAPER_ORIG_X
        y = VP_PLAN_Y0 - PAPER_ORIG_Y
        return x, y, VP_PLAN_W, VP_PLAN_H


def _find_sat_image(dxf_path):
    """Return absolute path of the satellite JPEG/PNG referenced by this DXF."""
    doc = ezdxf.readfile(dxf_path)
    layout = next((l for l in doc.layouts if l.name != "Model"), None)
    if layout is None:
        return None
    for e in layout:
        if e.dxftype() == "IMAGE":
            try:
                img_path = e.image_def.dxf.filename
                # Make absolute relative to DXF folder
                if not os.path.isabs(img_path):
                    img_path = os.path.join(os.path.dirname(dxf_path), img_path)
                if os.path.exists(img_path):
                    return img_path
            except Exception:
                pass
    return None


def _render_vectors(dxf_path, out_pdf):
    """Render DXF to a vector-only PDF (IMAGE entities removed)."""
    doc    = ezdxf.readfile(dxf_path)
    layout = next((l for l in doc.layouts if l.name != "Model"), None)
    if layout is None:
        return False

    # Patch unrenderable fonts
    for style in doc.styles:
        font = style.dxf.get("font", "").lower()
        if font in FONT_SUBS:
            style.dxf.font = FONT_SUBS[font]

    # Remove IMAGE entities so matplotlib doesn't rasterize them
    for e in list(layout):
        if e.dxftype() == "IMAGE":
            layout.delete_entity(e)

    fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor="none")
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("none")

    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)
    Frontend(ctx, out).draw_layout(layout, finalize=True)

    fig.set_size_inches(FIG_W, FIG_H)
    ax.set_aspect("auto")
    ax.set_xlim(PAPER_ORIG_X, PAPER_X1)
    ax.set_ylim(PAPER_ORIG_Y, PAPER_Y1)
    ax.axis("off")

    fig.savefig(out_pdf, dpi=100, bbox_inches=None, pad_inches=0, transparent=True)
    plt.close(fig)
    return True


def _render_satellite(img_path, x_mm, y_mm, w_mm, h_mm, out_pdf):
    """Embed satellite JPEG directly into a blank A1 PDF (no decompression)."""
    c = rl_canvas.Canvas(out_pdf, pagesize=(PAPER_W * MM, PAPER_H * MM))
    c.drawImage(img_path, x_mm * MM, y_mm * MM, w_mm * MM, h_mm * MM,
                preserveAspectRatio=False)
    c.save()


def _merge_pdfs(sat_pdf, vec_pdf, out_pdf):
    """Merge: satellite as background, vectors on top."""
    with pikepdf.open(sat_pdf) as sat, pikepdf.open(vec_pdf) as vec:
        sat_page = sat.pages[0]
        vec_page = vec.pages[0]

        # Add vector page as a Form XObject in the satellite PDF
        xobj = pikepdf.Page(vec_page).as_form_xobject()

        page_obj = sat_page.obj
        if "/Resources" not in page_obj:
            page_obj["/Resources"] = pikepdf.Dictionary()
        res = page_obj["/Resources"]
        if "/XObject" not in res:
            res["/XObject"] = pikepdf.Dictionary()
        res["/XObject"]["/VecLayer"] = sat.copy_foreign(xobj)

        # Append vector draw command to satellite content stream
        new_stream = sat.make_stream(b"q /VecLayer Do Q")
        existing = page_obj.get("/Contents")
        if existing is None:
            page_obj["/Contents"] = sat.make_indirect(new_stream)
        elif isinstance(existing, pikepdf.Array):
            existing.append(sat.make_indirect(new_stream))
        else:
            page_obj["/Contents"] = pikepdf.Array(
                [existing, sat.make_indirect(new_stream)]
            )

        sat.save(out_pdf)


def export_one(dxf_path):
    stem    = os.path.splitext(os.path.basename(dxf_path))[0]
    pdf_out = os.path.join(PDF_DIR, stem + ".pdf")

    sat_path = _find_sat_image(dxf_path)
    x_mm, y_mm, w_mm, h_mm = _sat_viewport(dxf_path)

    with tempfile.TemporaryDirectory() as tmp:
        vec_pdf = os.path.join(tmp, "vec.pdf")
        sat_pdf = os.path.join(tmp, "sat.pdf")

        # Step 1: vector-only PDF
        if not _render_vectors(dxf_path, vec_pdf):
            print(f"  [SKIP] {os.path.basename(dxf_path)}")
            return None

        vec_sz = os.path.getsize(vec_pdf) / 1024

        # Step 2: satellite layer (skip if no image)
        if sat_path:
            _render_satellite(sat_path, x_mm, y_mm, w_mm, h_mm, sat_pdf)
            sat_sz = os.path.getsize(sat_pdf) / 1024
            # Step 3: merge
            _merge_pdfs(sat_pdf, vec_pdf, pdf_out)
        else:
            import shutil
            shutil.copy(vec_pdf, pdf_out)
            sat_sz = 0

    final_sz = os.path.getsize(pdf_out) / 1024
    print(f"  [OK] {os.path.basename(pdf_out)}  "
          f"(vec={vec_sz:.0f}KB sat={sat_sz:.0f}KB total={final_sz:.0f}KB)")
    return pdf_out


def combine_channel_pdf(channel_name):
    """
    Merge key map + all sheets for a channel into one combined PDF.
    Page order: key map first, then sheets 01, 02, ... in order.
    Only the LATEST versioned PDF per sheet is used.
    """
    import re

    def _latest_only(pattern):
        """Return {type_key: path} keeping only the newest file per type, by mtime."""
        best = {}
        for f in glob.glob(os.path.join(PDF_DIR, pattern)):
            m = re.search(r'(Sheet\d+|KeyMap)_v\d+', f, re.IGNORECASE)
            if m:
                key = m.group(1).upper()
                mtime = os.path.getmtime(f)
                if key not in best or mtime > best[key][1]:
                    best[key] = (f, mtime)
        return {k: v[0] for k, v in best.items()}

    km     = _latest_only(f"{channel_name}-KeyMap_*.pdf")
    sheets = _latest_only(f"{channel_name}-Sheet*.pdf")

    if not km and not sheets:
        print(f"  No PDFs found for {channel_name}")
        return None

    def _sheet_num(key):
        m = re.search(r'(\d+)', key)
        return int(m.group(1)) if m else 0

    pages = []
    if "KEYMAP" in km:
        pages.append(km["KEYMAP"])
    pages.extend(sheets[k] for k in sorted(sheets, key=_sheet_num))
    out_path = os.path.join(PDF_COMBINED, f"{channel_name}.pdf")

    with pikepdf.Pdf.new() as combined:
        for p in pages:
            with pikepdf.open(p) as src:
                combined.pages.extend(src.pages)
        combined.save(out_path)

    n_pages = len(pages)
    sz_mb   = os.path.getsize(out_path) / 1024 / 1024
    print(f"  Combined: {os.path.basename(out_path)}  "
          f"({n_pages} pages, {sz_mb:.1f} MB)")
    return out_path


def _latest_dxfs(dxf_dir):
    """Return only the newest versioned DXF per (channel, sheet) pair, by last-write-time."""
    import re
    latest = {}
    for f in glob.glob(os.path.join(dxf_dir, "*.dxf")):
        name = os.path.basename(f)
        m = re.match(r'(.+-(Sheet\d+|KeyMap))_v\d+\.dxf$', name, re.IGNORECASE)
        if m:
            key = m.group(1).upper()
            mtime = os.path.getmtime(f)
            if key not in latest or mtime > latest[key][1]:
                latest[key] = (f, mtime)
    return sorted(v[0] for v in latest.values())


def _needs_export(dxf_path):
    """True if PDF doesn't exist or is older than the DXF."""
    stem    = os.path.splitext(os.path.basename(dxf_path))[0]
    pdf_out = os.path.join(PDF_DIR, stem + ".pdf")
    if not os.path.exists(pdf_out):
        return True
    return os.path.getmtime(dxf_path) > os.path.getmtime(pdf_out)


def _worker(dxf):
    """Multiprocessing worker — returns channel name on success, else None."""
    import re as _re, warnings
    warnings.filterwarnings("ignore")
    try:
        result = export_one(dxf)
        if result:
            stem = os.path.splitext(os.path.basename(dxf))[0]
            return _re.sub(r'-(Sheet\d+|KeyMap)_v\d+$', '', stem, flags=_re.IGNORECASE)
    except Exception as e:
        print(f"  [ERR] {os.path.basename(dxf)}: {e}")
    return None


def _cleanup_old_pdfs(pdf_dir):
    """Remove old-versioned individual PDFs, keeping only the latest per sheet."""
    import re
    latest = {}
    pdfs = glob.glob(os.path.join(pdf_dir, "*_v*.pdf"))
    for f in pdfs:
        name = os.path.basename(f)
        m = re.match(r'(.+-(Sheet\d+|KeyMap))_v\d+\.pdf$', name, re.IGNORECASE)
        if m:
            key = m.group(1).upper()
            mtime = os.path.getmtime(f)
            if key not in latest or mtime > latest[key][1]:
                latest[key] = (f, mtime)
    newest = {v[0] for v in latest.values()}
    removed = 0
    for f in pdfs:
        if f not in newest:
            try:
                os.remove(f)
                removed += 1
            except Exception:
                pass
    return removed


def _all_channel_names():
    """Return sorted list of all channel names that have at least one PDF."""
    import re
    names = set()
    for f in glob.glob(os.path.join(PDF_DIR, "*_v*.pdf")):
        m = re.match(r'(.+)-(Sheet\d+|KeyMap)_v\d+\.pdf$',
                     os.path.basename(f), re.IGNORECASE)
        if m:
            names.add(m.group(1))
    return sorted(names)


def rebuild_combined():
    """Delete all old versioned PDFs, keep only latest per sheet, rebuild every combined PDF."""
    print("Cleaning up old versioned PDFs...")
    removed = _cleanup_old_pdfs(PDF_DIR)
    if removed:
        print(f"  Removed {removed} old versioned PDFs.")
    else:
        print("  Nothing to remove.")

    channels = _all_channel_names()
    print(f"\nRebuilding combined PDFs for {len(channels)} channels...")
    for ch in channels:
        combine_channel_pdf(ch)
    print("\nDone.")


def main():
    from concurrent.futures import ProcessPoolExecutor, as_completed

    # Only process the latest version of each DXF
    all_latest = _latest_dxfs(DXF_DIR)
    # Skip DXFs whose PDF is already up to date
    todo = [f for f in all_latest if _needs_export(f)]

    if not todo:
        print("All PDFs are up to date.")
        # Still clean up and rebuild combined in case of stale state
        rebuild_combined()
        return

    workers = min(os.cpu_count() or 2, 8)
    print(f"Exporting {len(todo)}/{len(all_latest)} DXFs to PDF "
          f"({workers} parallel workers)...")
    print(f"Output: {PDF_DIR}\n")
    t0   = time.time()
    done = 0
    ok   = 0
    channels = set()

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_worker, dxf): dxf for dxf in todo}
        for fut in as_completed(futs):
            done += 1
            ch = fut.result()
            if ch:
                ok += 1
                channels.add(ch)
            if done % 10 == 0 or done == len(todo):
                pct = done / len(todo) * 100
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta  = (len(todo) - done) / rate if rate > 0 else 0
                print(f"  {done}/{len(todo)} ({pct:.0f}%)  "
                      f"ETA {eta/60:.1f} min", flush=True)

    print(f"\nDone -- {ok}/{len(todo)} PDFs in {time.time()-t0:.0f}s")

    # Clean up old versioned PDFs BEFORE combining so _latest_only sees only current files
    removed = _cleanup_old_pdfs(PDF_DIR)
    if removed:
        print(f"Removed {removed} old versioned PDFs.")

    # Rebuild ALL combined PDFs (not just touched channels) so every combined is fresh
    all_channels = _all_channel_names()
    print(f"\nRebuilding combined PDFs for {len(all_channels)} channels...")
    for ch in sorted(all_channels):
        combine_channel_pdf(ch)

    print(f"Output: {PDF_DIR}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--rebuild-combined":
        rebuild_combined()
    else:
        main()
