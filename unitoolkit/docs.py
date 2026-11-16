"""Document text: download a course file by id, extract text once, cache the text.

Why this exists: a Canvas file is a name until someone reads it. `display_name` is all the
API offers, and the words a student searches for are inside the PDF. So each file is fetched
once, parsed, and the *text* is kept while the bytes are discarded, keyed by
`file id + updated_at` so a reindex costs nothing and an updated file is re-read.

Extraction, in order of preference:
  pdf    pdftotext            (poppler; no dependency of ours)
  docx   zipfile + word/document.xml
  pptx   zipfile + ppt/slides/slideN.xml
  xlsx   zipfile + xl/sharedStrings.xml
  html   tag strip, entities decoded
  image  OCR (opt in), tesseract with the Swedish model

OCR is deliberately not part of every index run: it is slow (1.5-3 s per page at 300 dpi)
and most files do not need it. It runs when a document has no usable text layer, is capped,
and its result is cached like any other text.

    python -m unitoolkit docs course=2416                 # text layer only, fast
    python -m unitoolkit docs course=2416 ocr=true        # plus OCR for image-only files
"""
import hashlib
import html
import io
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import zipfile

from . import canvas

CACHE = pathlib.Path.home() / ".cache/unitoolkit/docs"

TEXT_EXTS = {"txt", "md", "csv", "json", "html", "htm", "xml", "py", "js", "css"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff"}
OFFICE_EXTS = {"docx", "pptx", "xlsx"}

# a document whose text is this thin is treated as image-only
MIN_USEFUL_CHARS = 100
MIN_CHARS_PER_PAGE = 40


def _have(binary):
    return shutil.which(binary) is not None


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------- extractors
def docx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        parts = [n for n in names if n == "word/document.xml" or
                 re.match(r"word/(header|footer)\d*\.xml$", n)]
        out = []
        for n in parts:
            xml = z.read(n).decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>|</w:tr>", "\n", xml)
            xml = re.sub(r"</w:tc>", " ", xml)
            out.append(re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", "", xml)))
        return "\n".join(out)


def pptx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        slides = []
        for n in z.namelist():
            m = re.match(r"ppt/slides/slide(\d+)\.xml$", n)
            if m:
                slides.append((int(m.group(1)), n))
        out = []
        for num, n in sorted(slides):
            xml = z.read(n).decode("utf-8", "ignore")
            xml = re.sub(r"</a:p>", "\n", xml)
            txt = re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", "", xml)).strip()
            if txt:
                out.append(f"Bild {num}: {txt}")
        return "\n".join(out)


def xlsx_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if "xl/sharedStrings.xml" not in z.namelist():
            return ""
        xml = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
        cells = re.findall(r"<t[^>]*>(.*?)</t>", xml, re.S)
        return "\n".join(html.unescape(c) for c in cells)


def html_text(data):
    s = data.decode("utf-8", "ignore")
    s = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"</(p|div|li|tr|h[1-6])>|<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r" *\n *", "\n", re.sub(r"\n{2,}", "\n", s)).strip()


def pdf_text(data):
    """Text layer via pdftotext. Page breaks survive as \\f, which gives us page numbers."""
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "in.pdf"
        src.write_bytes(data)
        r = _run(["pdftotext", "-enc", "UTF-8", "-q", str(src), "-"])
        return r.stdout or ""


def pdf_pages(data):
    """Real page count from the PDF itself, so a capped OCR run can say it was capped."""
    if not _have("pdfinfo"):
        return None
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "in.pdf"
        src.write_bytes(data)
        r = _run(["pdfinfo", str(src)])
        m = re.search(r"^Pages:\s+(\d+)", r.stdout or "", re.M)
        return int(m.group(1)) if m else None


def ocr_pdf(data, max_pages=60, dpi=300, lang="swe"):
    """Render pages to PNG and OCR them. Returns (text, pages_ocred)."""
    if not (_have("pdftoppm") and _have("tesseract")):
        return "", 0
    out = []
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "in.pdf"
        src.write_bytes(data)
        pre = str(pathlib.Path(td) / "pg")
        _run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", "1", "-l", str(max_pages),
              str(src), pre])
        pages = sorted(pathlib.Path(td).glob("pg*.png"))
        for i, p in enumerate(pages, 1):
            r = _run(["tesseract", str(p), "-", "-l", lang, "--psm", "3"])
            out.append(r.stdout.strip())
    return "\f".join(out), len(out)


def ocr_image(data, lang="swe"):
    if not _have("tesseract"):
        return ""
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "in.png"
        src.write_bytes(data)
        return _run(["tesseract", str(src), "-", "-l", lang, "--psm", "3"]).stdout.strip()


def extract(name, data, allow_ocr=False, ocr_max_pages=60):
    """Extract text from bytes. Returns dict: text, kind, ocr_pages, needs_ocr."""
    ext = (name.rsplit(".", 1)[-1] or "").lower()
    info = {"kind": ext, "ocr_pages": 0, "needs_ocr": False, "text": ""}

    if ext == "pdf":
        text = pdf_text(data)
        pages = max(1, text.count("\f") + 1)
        thin = len(text.strip()) < MIN_USEFUL_CHARS or len(text.strip()) / pages < MIN_CHARS_PER_PAGE
        info["text"] = text
        if thin:
            info["needs_ocr"] = True
            info["pages_total"] = pdf_pages(data)
            if allow_ocr:
                ocr, n = ocr_pdf(data, max_pages=ocr_max_pages)
                info["ocr_pages"] = n
                # a capped run must be visible, or a half-read book looks complete
                info["ocr_truncated"] = bool(info["pages_total"] and n < info["pages_total"])
                if len(ocr.strip()) > len(text.strip()):
                    info["text"] = ocr
        return info
    if ext == "docx":
        info["text"] = docx_text(data)
    elif ext == "pptx":
        info["text"] = pptx_text(data)
    elif ext == "xlsx":
        info["text"] = xlsx_text(data)
    elif ext in ("html", "htm", "aspx"):
        info["text"] = html_text(data)
    elif ext in TEXT_EXTS:
        info["text"] = data.decode("utf-8", "ignore")
    elif ext in IMAGE_EXTS:
        info["needs_ocr"] = True
        if allow_ocr:
            info["text"] = ocr_image(data)
            info["ocr_pages"] = 1 if info["text"] else 0
    else:
        info["kind"] = "binary"
    return info


# ---------------------------------------------------------------- cache
def _cache_dir(course_id):
    d = CACHE / str(course_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_key(file_meta):
    return hashlib.sha1(
        f"{file_meta.get('id')}:{file_meta.get('updated_at') or file_meta.get('created_at')}"
        .encode()).hexdigest()[:16]


def read_cached(course_id, file_meta):
    p = _cache_dir(course_id) / f"{cache_key(file_meta)}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001 - a broken cache entry is just a miss
            return None
    return None


def write_cached(course_id, file_meta, payload):
    p = _cache_dir(course_id) / f"{cache_key(file_meta)}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False))
    return p


# ---------------------------------------------------------------- build
def build(course_id, ocr=False, force=False, max_files=None, ocr_max_pages=60,
          max_bytes=40 * 1024 * 1024, only_contains=None, progress=None):
    """Extract text for every file in a course. Cached by file id + updated_at."""
    files = canvas.try_get(f"courses/{course_id}/files", per_page=100) or []
    files = [f for f in files if isinstance(f, dict)]
    files.sort(key=lambda f: (f.get("display_name") or "").lower())
    if only_contains:
        needle = only_contains.lower()
        files = [f for f in files
                 if needle in (f.get("display_name") or f.get("filename") or "").lower()]
    if max_files:
        files = files[:max_files]

    stats = {"course": course_id, "files": len(files), "cached": 0, "extracted": 0,
             "failed": 0, "skipped_big": 0, "needs_ocr": 0, "ocr_pages": 0,
             "with_text": 0, "no_text": 0, "chars": 0, "by_kind": {}}
    for i, f in enumerate(files, 1):
        name = f.get("display_name") or f.get("filename") or str(f.get("id"))
        if progress:
            progress(f"{i}/{len(files)} {name[:44]}")
        if (f.get("size") or 0) > max_bytes:
            stats["skipped_big"] += 1
            continue
        hit = None if force else read_cached(course_id, f)
        if hit and ocr and hit.get("needs_ocr") and len((hit.get("text") or "").strip()) < MIN_USEFUL_CHARS:
            hit = None  # cached as image-only, and OCR is now requested: read it again
            stats["re_ocred"] = stats.get("re_ocred", 0) + 1
        if hit:
            stats["cached"] += 1
            payload = hit
        else:
            url = f.get("url") or f"courses/{course_id}/files/{f.get('id')}/download"
            try:
                data = canvas.download_bytes(url)
            except Exception:  # noqa: BLE001 - a failed file must not stop the run
                stats["failed"] += 1
                continue
            info = extract(name, data, allow_ocr=ocr, ocr_max_pages=ocr_max_pages)
            stats["extracted"] += 1
            stats["ocr_pages"] += info["ocr_pages"]
            payload = {"id": f.get("id"), "name": name, "ext": info["kind"],
                       "updated_at": f.get("updated_at") or f.get("created_at") or "",
                       "href": f"/courses/{course_id}/files/{f.get('id')}",
                       "url": url, "bytes": len(data), "pages": info["text"].count("\f") + 1,
                       "pages_total": info.get("pages_total"),
                       "ocr_pages": info["ocr_pages"], "needs_ocr": info["needs_ocr"],
                       "ocr_truncated": bool(info.get("ocr_truncated")),
                       "text": info["text"]}
            if payload["ocr_truncated"]:
                stats["ocr_truncated"] = stats.get("ocr_truncated", 0) + 1
            write_cached(course_id, f, payload)
        kind = payload.get("ext") or "?"
        stats["by_kind"][kind] = stats["by_kind"].get(kind, 0) + 1
        n = len((payload.get("text") or "").strip())
        stats["chars"] += n
        if n >= MIN_USEFUL_CHARS:
            stats["with_text"] += 1
        else:
            stats["no_text"] += 1
            if payload.get("needs_ocr"):
                stats["needs_ocr"] += 1
    return stats


HREF_RE = re.compile(r'href="([^"]+)"', re.I)


def page_links(course_id, mirror_root=None):
    """Every link inside the course's pages, deduped, classified.

    Pages line up with the mirror; without a mirror nothing is read, which keeps this from
    turning into a page crawl of its own.
    """
    root = pathlib.Path(mirror_root or (pathlib.Path.home() / "courses/canvas" / str(course_id)))
    pages = root / "pages"
    if not pages.exists():
        return []
    seen, out = set(), []
    for p in sorted(pages.glob("*.html")):
        for url in HREF_RE.findall(p.read_text(errors="ignore")):
            url = html.unescape(url).strip()
            if not url or url.startswith(("#", "mailto:", "javascript:")) or url in seen:
                continue
            seen.add(url)
            kind = "other"
            if re.search(r"/files/\d+", url):
                kind = "canvas_file"
            elif re.search(r"/pages/", url):
                kind = "canvas_page"
            elif re.search(r"sharepoint\.com/:[a-z]:/", url, re.I):
                kind = "share"
            elif re.search(r"(kaltura|youtube|vimeo)", url, re.I):
                kind = "video"
            out.append({"url": url, "kind": kind, "from": p.name})
    return out


def resolve_links(course_id, limit=None, allow_ocr=False, progress=None):
    """Fetch linked documents that are not in the file tree (SharePoint share links) and
    cache their text alongside the course files. Bytes are read once and dropped."""
    from . import sharepoint

    links = [l for l in page_links(course_id) if l["kind"] == "share"]
    if limit:
        links = links[:limit]
    stats = {"course": course_id, "share_links": len(links), "stored": 0, "cached": 0,
             "failed": 0, "with_text": 0, "no_text": 0, "chars": 0, "by_kind": {}}
    for i, link in enumerate(links, 1):
        url = link["url"]
        if progress:
            progress(f"{i}/{len(links)} {url[-28:]}")
        meta = {"id": f"share:{url}", "updated_at": "fixed"}  # a share link has no version
        hit = read_cached(course_id, meta)
        if hit:
            stats["cached"] += 1
            payload = hit
        else:
            body, kind, name = sharepoint.fetch_share(url)
            if not body:
                stats["failed"] += 1
                continue
            name = name or f"share-{len(body)}.{kind}"
            info = extract(name, body, allow_ocr=allow_ocr)
            stats["stored"] += 1
            payload = {"id": meta["id"], "name": name, "ext": info["kind"],
                       "updated_at": "", "href": url, "url": url,
                       "bytes": len(body), "pages": info["text"].count("\f") + 1,
                       "pages_total": info.get("pages_total"),
                       "ocr_pages": info["ocr_pages"], "needs_ocr": info["needs_ocr"],
                       "ocr_truncated": bool(info.get("ocr_truncated")),
                       "text": info["text"]}
            write_cached(course_id, meta, payload)
        stats["by_kind"][payload.get("ext") or "?"] = stats["by_kind"].get(payload.get("ext") or "?", 0) + 1
        n = len((payload.get("text") or "").strip())
        stats["chars"] += n
        stats["with_text" if n >= MIN_USEFUL_CHARS else "no_text"] += 1
    return stats


def load(course_id):
    """Cached documents for a course, shaped like search docs."""
    out = []
    for p in sorted(_cache_dir(course_id).glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            continue
        text = d.get("text") or ""
        if not text.strip():
            continue
        out.append({"type": "fildok", "title": d.get("name") or "(fil)",
                    "body": text, "href": d.get("href") or "#",
                    "ext": d.get("ext") or "", "date": (d.get("updated_at") or "")[:10],
                    "pages": d.get("pages"), "ocr_pages": d.get("ocr_pages") or 0,
                    "bytes": d.get("bytes")})
    return out


def page_of(text, at):
    """1-based page number for a character offset, using pdftotext's form feeds."""
    if not text:
        return None
    return text.count("\f", 0, max(0, at)) + 1


def clear(course_id=None):
    target = CACHE / str(course_id) if course_id else CACHE
    if target.exists():
        shutil.rmtree(target)
    return {"cleared": str(target)}
