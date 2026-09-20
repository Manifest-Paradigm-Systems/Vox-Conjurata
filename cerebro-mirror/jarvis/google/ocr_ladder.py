"""Turn a file into text — and never lie about why it could not.

ONE EXTRACTION PATH FOR EVERYTHING THAT HAS ONE. `drive_index.py` and
`attachment_index.py` each grew their own copy of this, and the copies drifted into
the same bug in different shapes. Both are now callers of this module.

THE CONTRACT IS THE POINT, and it is three values rather than one:

    (text, source, error)

    text == "" and error is None   -> we looked, and there is nothing there
    text == "" and error is set    -> we could NOT look, and here is what was missing

The original bug was that these two collapsed into one. `_run()` caught
`FileNotFoundError`, returned `""`, and `text_from_pdf()` read that as "this document
has no text" — so on a host without poppler, 2,713 PDFs were written into the index as
*textless* rather than as *unread*. No error appeared anywhere. It looked exactly like
a Drive full of scanned images, and it stayed that way because the difference was
invisible. An empty string must never mean two things.

THE LADDER, cheapest first. Each rung is tried only if the one above it produced
nothing worth keeping:

    PDF with a text layer   ->  pdftotext -layout        (instant, exact)
    PDF without one         ->  pdftoppm + tesseract     (a scan)
    multi-frame TIFF        ->  every frame, OCR'd       (see below)
    images                  ->  tesseract
    DOCX / XLSX / PPTX      ->  unzip + strip XML        (no OCR needed, exact)
    HTML / EPUB             ->  strip tags               (no OCR needed, exact)

MULTI-FRAME TIFFS WERE BEING DESTROYED BEFORE ANY OF THIS RAN. `LifePacket-AI`'s
`core/ingestion.py` converts images with `Image.open(p).save(out, "PDF")`, with no
`ImageSequence` — so a 22-frame DD-4 packet became a 1-page PDF, and the OCR pass after
it read page 1 of that. Measured across the Army service record: 100 TIFFs, 43 of them
multi-frame, 215 frames total, against 174 documents that were recorded as complete.
About 190 pages were never read at any stage, and nothing said so. Frame iteration
belongs here, at the point of extraction, where it cannot be skipped by an upstream
converter.

WHERE IT RUNS. Workhorse has poppler and tesseract natively. Cerebro does not and is
rpm-ostree, so installing them means layering packages and rebooting the box that runs
the brain, mail-api and the panel. Instead this module falls back to a container
(`sandbox/Containerfile.ocr`) — but ONE container per BATCH, not per file. A podman
start is ~0.4 s, and 2,713 PDFs would spend eighteen minutes doing nothing but starting
containers. The driver inside the image (`sandbox/ocr_driver.py`) takes a whole work
list on stdin and returns the whole result on stdout.

The container runs `--network=none`. It never needs the network, and a medical PDF
being OCR'd should not be able to reach anything even if something in it tried.

Usage:
    python3 ocr_ladder.py probe                 # which tools exist here
    python3 ocr_ladder.py text <path>           # extract one file, print the text
    python3 ocr_ladder.py json <path> [path...] # extract many, print the contract
"""

from __future__ import annotations

import base64
import html as _html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# tesseract | eyes | off.  `off` disables OCR entirely, which is the right setting on a
# host where it is unavailable and the caller would rather have "unread" than a
# container pull in the middle of a crawl.
OCR_ENGINE = os.getenv("JARVIS_OCR", "tesseract")
EYES_URL = os.getenv("JARVIS_EYES_URL", "http://192.168.0.67:8084")

# How many pages of a PDF to rasterise before giving up. 0 means all of them, which is
# what the service-record import wants and what a Drive crawl does not (a 400-page scan
# would dominate a run for one document nobody asked about).
MAX_PAGES = int(os.getenv("JARVIS_OCR_MAX_PAGES", "20"))

# Below this many characters, a PDF's text layer is treated as absent. Some scanners
# write an empty or one-line layer that would otherwise shadow the real content.
MIN_TEXT = 40

OCR_IMAGE = os.getenv("JARVIS_OCR_IMAGE", "localhost/jarvis-ocr")
PODMAN = os.getenv("JARVIS_PODMAN", "podman")

# What we can turn into words, and how. Anything not here is handled by the caller as
# "not indexable" — a decision that belongs to the caller, not to this module.
PDF_MIMES = ("application/pdf",)
IMAGE_PREFIX = "image/"
OFFICE_MIMES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
)
HTML_MIMES = ("text/html", "application/xhtml+xml")
EPUB_MIMES = ("application/epub+zip",)
TEXT_MIMES = ("text/plain", "text/markdown", "text/csv", "application/json")

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")


# ---------------------------------------------------------------- tool discovery
_TOOLS: dict[str, bool] = {}


def have(tool: str) -> bool:
    """Is this binary on PATH? Cached — a crawl asks thousands of times."""
    if tool not in _TOOLS:
        _TOOLS[tool] = shutil.which(tool) is not None
    return _TOOLS[tool]


def probe() -> dict:
    """What this host can do, for a caller that wants to report it rather than guess."""
    tools = {t: have(t) for t in ("pdftotext", "pdftoppm", "tesseract", "convert")}
    return {
        "tools": tools,
        "ocr_engine": OCR_ENGINE,
        "container": container_available(),
        "max_pages": MAX_PAGES,
        # Only meaningful if a PDF path is actually usable here.
        "pdf": tools["pdftotext"],
        "images": tools["tesseract"] or OCR_ENGINE == "eyes",
    }


def container_available() -> bool:
    if not shutil.which(PODMAN):
        return False
    try:
        r = subprocess.run([PODMAN, "image", "exists", OCR_IMAGE],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def run(cmd: list[str], timeout: int = 600) -> tuple[str, str | None]:
    """Run a command. Returns (stdout, error).

    THIS IS THE FIX. The old `_run` returned "" for both "the tool printed nothing" and
    "the tool is not installed", which is how a missing dependency became 2,713
    documents recorded as having no text. A missing binary is now named.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return "", f"unavailable:{cmd[0]}"
    except subprocess.TimeoutExpired:
        return "", f"timeout:{cmd[0]}"
    except OSError as exc:
        return "", f"unavailable:{cmd[0]}:{type(exc).__name__}"
    # A non-zero exit with output is common and fine (tesseract warns loudly on old
    # TIFFs and still returns good text), so only a genuinely empty failure is an error.
    if r.returncode != 0 and not (r.stdout or "").strip():
        detail = (r.stderr or "").strip().splitlines()
        return "", f"failed:{cmd[0]}:{(detail[0][:80] if detail else r.returncode)}"
    return r.stdout or "", None


# ---------------------------------------------------------------- images
def _join(pages: list[str], mark_pages: bool) -> str:
    """Join per-page text, optionally labelling each page.

    Page labels are not decoration. A service record is 21 pages in one file and the
    answer to "when did I separate" is on one of them; "[page 7]" is the difference
    between a citation someone can check and "somewhere in this document". The markers
    are also counted by `read_document` to answer "how long is this".
    """
    if not mark_pages:
        return "\n\n".join(p for p in pages if p).strip()
    return "\n\n".join(f"[page {i}]\n\n{p}" if p else f"[page {i}]\n\n(no text)"
                       for i, p in enumerate(pages, 1)).strip()


def _imagemagick() -> str:
    """ImageMagick 7 renamed `convert` to `magick`. Take whichever exists, newest first."""
    if have("magick"):
        return "magick"
    return "convert"


def image_text(path: str, max_pages: int | None = None,
               mark_pages: bool = False) -> tuple[str, str, str | None]:
    """OCR one image, EVERY FRAME of it if it is a multi-frame TIFF.

    tesseract reads frame 1 only and does not say so — a 22-frame DD-4 packet OCR'd as
    its cover sheet. The frame count is therefore read first and each frame is rendered
    on its own.

    (A 1-bit bilevel G4 TIFF was suspected of a second failure here and was NOT one:
    measured against the rendered PNG, raw and converted agree character-for-character at
    every segmentation mode. The apparent difference was `--psm`, which is handled in
    `_ocr_one`. Worth recording, because "the old TIFF format confused it" was a
    satisfying explanation and a wrong one.)
    """
    if OCR_ENGINE == "off":
        return "", "", "unavailable:ocr-disabled"

    frames = _frame_count(path)
    if frames <= 1:
        text, err = _ocr_one(path)
        return (text, f"ocr:{OCR_ENGINE}", err) if text else ("", "", err)

    limit = frames if not max_pages else min(frames, max_pages)
    out: list[str] = []
    first_err: str | None = None
    tmp = tempfile.mkdtemp(prefix="imgocr-")
    try:
        for i in range(limit):
            png = os.path.join(tmp, f"frame-{i:04d}.png")
            _, cerr = run([_imagemagick(), f"{path}[{i}]", "-background", "white",
                           "-alpha", "remove", "-alpha", "off", png])
            if cerr or not os.path.exists(png):
                first_err = first_err or cerr or f"render:frame{i}"
                out.append("")
                continue
            text, err = _ocr_one(png)
            if err:
                first_err = first_err or err
            # Every frame is appended, including the ones that yielded nothing, so that
            # [page N] keeps counting frames rather than renumbering around a blank one.
            out.append(text or "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not any(out):
        return "", "", first_err
    return _join(out, mark_pages), f"ocr:{OCR_ENGINE}", None


def _frame_count(path: str) -> int:
    """How many frames this image has. 1 when we cannot tell — never a crash."""
    if not path.lower().endswith((".tif", ".tiff")):
        return 1
    try:
        from PIL import Image                                     # noqa: PLC0415
        with Image.open(path) as im:
            return int(getattr(im, "n_frames", 1) or 1)
    except Exception:                                             # noqa: BLE001
        # Pillow absent or the file is malformed: fall through to `identify`, and if
        # that is absent too, treat it as single-frame. A guess of 1 is the old
        # behaviour, so this can only ever be better than before.
        out, err = run(["identify", "-format", "%n\n", path])
        if not err:
            try:
                return max(int(x) for x in out.split() if x.strip().isdigit())
            except ValueError:
                pass
    return 1


# Page segmentation modes to try, in order, stopping at the first that returns text.
#
# 3 is tesseract's default — full automatic segmentation — and is right for ordinary
# pages. But it returns NOTHING, with exit 0, for some layouts: the 1974 dependent birth
# certificate in the service record is 22% ink and came back empty at 3, 4 and 11 while 6
# read it cleanly. Because the exit code is 0, that was indistinguishable from a blank
# page — a records document silently recorded as having no text, which is the exact
# failure this module exists to remove.
#
# 6 assumes a single uniform block of text. It is wrong for a two-column page, which is
# why it is a fallback rather than the default: a mode that reads *something* on a
# document the automatic pass could not place is better than reporting nothing, and the
# cost is one extra tesseract run only on pages that produced no text at all.
PSM_LADDER = ("3", "6", "4")


def _ocr_one(path: str) -> tuple[str, str | None]:
    if OCR_ENGINE == "eyes":
        return _ocr_eyes(path)
    last_err: str | None = None
    for psm in PSM_LADDER:
        text, err = run(["tesseract", path, "stdout", "-l", "eng", "--psm", psm])
        if err and not text:
            return "", err            # the tool itself failed — a different thing
        if text.strip():
            return text.strip(), None
        last_err = None
    return "", last_err


def _ocr_eyes(path: str) -> tuple[str, str | None]:
    """MiniCPM-V on cerebro. Better on messy scans; far slower; one image at a time."""
    import urllib.error                                        # noqa: PLC0415
    import urllib.request                                      # noqa: PLC0415
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        mime = "image/png"
        if path.lower().endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        elif path.lower().endswith((".tif", ".tiff")):
            mime = "image/tiff"
        payload = {
            "model": "vision",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Transcribe ALL text in this image exactly. "
                                         "If there is no text, reply with nothing."},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,"
                                      f"{base64.b64encode(data).decode()}"}}]}],
            # 800 was a truncation, not a limit: a dense form page runs well past it,
            # and a half-transcribed document looks like a complete one.
            "max_tokens": 4000,
        }
        req = urllib.request.Request(
            f"{EYES_URL}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            d = json.loads(r.read())
        return (d["choices"][0]["message"]["content"] or "").strip(), None
    except Exception as exc:                                      # noqa: BLE001
        return "", f"eyes:{type(exc).__name__}"


# ---------------------------------------------------------------- pdfs
def pdf_text(path: str, max_pages: int | None = None,
             mark_pages: bool = False) -> tuple[str, str, str | None]:
    """Text from a PDF: the text layer if it has one, OCR of the pages if it does not.

    `pdftotext` emits the whole document at once, so its output cannot be split per page
    without a second pass — and that is why a text-layer PDF gets no page markers below
    one call. The OCR branch, which is where the scanned records live, does have them.
    """
    text, err = run(["pdftotext", "-layout", "-q", path, "-"])
    if err:
        return "", "", err
    if len(text.strip()) > MIN_TEXT:
        if mark_pages and "\f" in text:
            # pdftotext separates pages with a form feed. Splitting on it costs nothing
            # and turns "somewhere in this 21-page file" into "[page 7]" — the same
            # guarantee the OCR branch gives, and without it a text-layer PDF would
            # report as one page however long it is.
            return _join(text.split("\f"), True), "pdftotext", None
        return text.strip(), "pdftotext", None

    if OCR_ENGINE == "off":
        # Genuinely nothing to return, but not a failure — the layer was absent.
        return "", "", None

    limit = max_pages if max_pages is not None else MAX_PAGES
    tmp = tempfile.mkdtemp(prefix="pdfocr-")
    try:
        cmd = ["pdftoppm", "-r", "200", "-png"]
        if limit:
            cmd += ["-l", str(limit)]
        cmd += [path, os.path.join(tmp, "page")]
        _, err = run(cmd)
        if err:
            return "", "", err
        pages = sorted(f for f in os.listdir(tmp) if f.endswith(".png"))
        out: list[str] = []
        first_err: str | None = None
        for p in pages[:limit] if limit else pages:
            t, e = _ocr_one(os.path.join(tmp, p))
            if e:
                first_err = first_err or e
            out.append(t or "")
        if not any(out):
            return "", "", first_err
        return _join(out, mark_pages), f"ocr:{OCR_ENGINE}", None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- everything else
def office_text(path: str) -> tuple[str, str, str | None]:
    """A .docx/.xlsx/.pptx is a zip of XML. Exact text, no OCR, no dependency."""
    import zipfile                                            # noqa: PLC0415
    try:
        with zipfile.ZipFile(path) as z:
            parts = [n for n in z.namelist()
                     if re.match(r"word/document\.xml|xl/sharedStrings\.xml|"
                                 r"ppt/slides/slide\d+\.xml", n)]
            if not parts:
                return "", "", None
            chunks = []
            for name in sorted(parts):
                xml = z.read(name).decode("utf-8", "replace")
                xml = re.sub(r"(?i)</w:p>|</a:p>|<w:tab/>", "\n", xml)
                chunks.append(_html.unescape(re.sub(r"<[^>]+>", " ", xml)))
            return "\n".join(chunks).strip(), "office-xml", None
    except zipfile.BadZipFile:
        return "", "", None
    except OSError as exc:
        return "", "", f"unavailable:zip:{type(exc).__name__}"


def html_text(data: bytes) -> tuple[str, str, str | None]:
    text = data.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip(), "html", None


def epub_text(path: str) -> tuple[str, str, str | None]:
    """An EPUB is a zip of XHTML. Read the spine order if we can, else every document."""
    import zipfile                                            # noqa: PLC0415
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist()
                     if n.lower().endswith((".xhtml", ".html", ".htm"))]
            if not names:
                return "", "", None
            order: list[str] = []
            try:
                container = z.read("META-INF/container.xml").decode("utf-8", "replace")
                m = re.search(r'full-path="([^"]+\.opf)"', container)
                if m:
                    opf = z.read(m.group(1)).decode("utf-8", "replace")
                    base = os.path.dirname(m.group(1))
                    ids = dict(re.findall(r'<item\s+id="([^"]+)"[^>]*href="([^"]+)"', opf))
                    order = [os.path.join(base, ids[i]) for i in
                             re.findall(r'idref="([^"]+)"', opf) if i in ids]
            except (KeyError, OSError):
                order = []
            use = [n for n in order if n in names] or sorted(names)
            chunks = []
            for n in use:
                t, _, err = html_text(z.read(n))
                if err:
                    return "", "", err
                if t:
                    chunks.append(t)
            return "\n\n".join(chunks).strip(), "epub", None
    except zipfile.BadZipFile:
        return "", "", None
    except OSError as exc:
        return "", "", f"unavailable:zip:{type(exc).__name__}"


def plain_text(path: str) -> tuple[str, str, str | None]:
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace").strip(), "text", None
    except OSError as exc:
        return "", "", f"unavailable:open:{type(exc).__name__}"


# ---------------------------------------------------------------- the one entry point
def extract_path(path: str, mime: str = "", max_pages: int | None = None,
                 ocr: bool = True, mark_pages: bool = False
                 ) -> tuple[str, str, str | None]:
    """Text from a file on disk. The only function callers need."""
    mime = (mime or "").lower()
    ext = os.path.splitext(path)[1].lower()
    if not mime:
        # Fall back to the extension when the caller has no MIME — a local tree of
        # scans has filenames and nothing else.
        if ext == ".pdf":
            mime = "application/pdf"
        elif ext in IMAGE_EXT:
            mime = "image/" + ext.lstrip(".")
        elif ext == ".epub":
            mime = "application/epub+zip"
        elif ext in (".html", ".htm", ".xhtml"):
            mime = "text/html"
        elif ext == ".docx":
            mime = OFFICE_MIMES[0]
        else:
            mime = "text/plain"

    if mime in PDF_MIMES:
        return pdf_text(path, max_pages, mark_pages)
    if mime.startswith(IMAGE_PREFIX) or ext in IMAGE_EXT:
        if not ocr:
            return "", "", "unavailable:ocr-disabled"
        return image_text(path, max_pages, mark_pages)
    if mime in OFFICE_MIMES:
        return office_text(path)
    if mime in HTML_MIMES:
        try:
            with open(path, "rb") as fh:
                return html_text(fh.read())
        except OSError as exc:
            return "", "", f"unavailable:open:{type(exc).__name__}"
    if mime in EPUB_MIMES:
        return epub_text(path)
    if mime.startswith("text/") or mime in TEXT_MIMES:
        return plain_text(path)
    return "", "", None            # a type this module does not claim to read


def extract_bytes(data: bytes, mime: str = "", name: str = "",
                  max_pages: int | None = None, ocr: bool = True,
                  mark_pages: bool = False) -> tuple[str, str, str | None]:
    """Text from bytes in memory — what a download gives you.

    Writes to a temp file first. The alternative is a second copy of every rung of the
    ladder that takes bytes, which is how the two original copies drifted apart.
    """
    if not data:
        return "", "", None
    suffix = os.path.splitext(name or "")[1] or _suffix_for(mime)
    tmp = tempfile.mkdtemp(prefix="ocrc-")
    try:
        path = os.path.join(tmp, "content" + suffix)
        with open(path, "wb") as fh:
            fh.write(data)
        return extract_path(path, mime, max_pages, ocr, mark_pages)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _suffix_for(mime: str) -> str:
    return {
        "application/pdf": ".pdf",
        "application/epub+zip": ".epub",
        "text/html": ".html",
        "application/xhtml+xml": ".html",
    }.get((mime or "").lower(), ".bin" if not (mime or "").startswith("text/") else ".txt")


# ---------------------------------------------------------------- batched container
def extract_many(items: list[tuple], max_pages: int | None = None
                 ) -> dict[tuple, tuple[str, str, str | None]]:
    """Extract many files at once. items = [(key, path, mime), ...].

    ONE ENTRY POINT FOR BOTH HOSTS. Where the tools are installed (Workhorse) this runs
    them in-process, one file at a time. Where they are not (cerebro) it stages
    everything into a single directory and makes ONE container run for the batch —
    because a podman start is ~0.4 s, about what a tesseract pass costs, so a container
    per file would double the wall clock of a 2,713-file crawl and buy nothing.

    Returns {key: (text, source, error)}. If neither route is available, every key comes
    back with `unavailable:container` — which the caller stores as "unread", never as
    "empty", because that distinction is the reason this module exists.
    """
    results: dict[tuple, tuple[str, str, str | None]] = {}
    if not items:
        return results

    # Native first: if this host can read the formats itself, there is nothing a
    # container would add except latency.
    if have("pdftotext") or OCR_ENGINE == "eyes":
        for key, path, mime in items:
            try:
                results[key] = extract_path(path, mime, max_pages)
            except Exception as exc:                              # noqa: BLE001
                results[key] = ("", "", f"extract:{type(exc).__name__}")
        return results

    if not container_available():
        for key, _path, _mime in items:
            results[key] = ("", "", "unavailable:container")
        return results

    stage = tempfile.mkdtemp(prefix="ocrbatch-")
    try:
        payload = []
        for i, (key, path, mime) in enumerate(items):
            target = os.path.join(stage, f"{i:05d}{os.path.splitext(path)[1] or ''}")
            try:
                shutil.copyfile(path, target)
            except OSError as exc:
                results[key] = ("", "", f"unavailable:stage:{type(exc).__name__}")
                continue
            payload.append({"key": i, "path": os.path.basename(target),
                            "mime": mime or "", "max_pages": max_pages})

        if not payload:
            return results

        # `--security-opt label=disable` IS NOT OPTIONAL ON FEDORA. The staged files
        # carry the host's SELinux label, which the container's policy does not permit it
        # to read, so every mount came back "Permission denied" — and pdftotext reports
        # that as a bare exit 1 with no output, which the ladder would have recorded as a
        # failed read on all 2,713 documents. (It also gets confused for a corrupt file.)
        # Disabling the label check for this one container is the same fix the vision
        # containers needed, and the mount is read-only and short-lived.
        cmd = [PODMAN, "run", "--rm", "-i", "--network=none",
               "--security-opt", "label=disable",
               "-v", f"{stage}:/in:ro", OCR_IMAGE, "ocr_driver.py"]
        try:
            r = subprocess.run(cmd, input=json.dumps({"files": payload}),
                               capture_output=True, text=True, timeout=3600)
        except (subprocess.SubprocessError, OSError) as exc:
            for key, _p, _m in items:
                results.setdefault(key, ("", "", f"container:{type(exc).__name__}"))
            return results

        if r.returncode != 0 and not (r.stdout or "").strip():
            detail = (r.stderr or "").strip().splitlines()
            msg = f"container:{(detail[0][:100] if detail else r.returncode)}"
            for key, _p, _m in items:
                results.setdefault(key, ("", "", msg))
            return results

        # JSONL, one object per line. A crash part-way through the batch leaves the
        # lines already written intact, so the last document is lost rather than the
        # whole run — and a caller can never mistake a truncated batch for a complete
        # one that happened to find nothing, because every item is filled in below.
        by_index = {i: key for i, (key, _p, _m) in enumerate(items)}
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            key = by_index.get(row.get("key"))
            if key is not None:
                results[key] = (row.get("text") or "", row.get("source") or "",
                                row.get("error") or None)

        for key, _p, _m in items:
            results.setdefault(key, ("", "", "container:no-result"))
        return results
    finally:
        shutil.rmtree(stage, ignore_errors=True)


# ---------------------------------------------------------------- cli
def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]

    if cmd == "probe":
        print(json.dumps(probe(), indent=2))
        return 0

    if cmd == "text":
        if len(argv) < 3:
            raise SystemExit("usage: ocr_ladder.py text <path>")
        text, source, error = extract_path(argv[2], max_pages=0)
        print(f"--- source={source or '(none)'} error={error or '(none)'} "
              f"chars={len(text)}")
        print(text[:2000])
        return 0 if error is None else 2

    if cmd == "json":
        if len(argv) < 3:
            raise SystemExit("usage: ocr_ladder.py json <path> [path...]")
        items = [(i, p, "") for i, p in enumerate(argv[2:])]
        out = []
        for key, (text, source, error) in extract_many(items, max_pages=0).items():
            out.append({"path": argv[2 + key], "source": source, "error": error,
                        "chars": len(text), "head": text[:120]})
        print(json.dumps(out, indent=2))
        return 0 if all(r["error"] is None for r in out) else 2

    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
