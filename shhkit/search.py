"""Full-text search over a Canvas course: from the live API or from a mirror on disk.

Same semantics as the Canvas Text Search extension (canvas-text-search repo), so an agent
and a student searching the same course get comparable answers: implicit AND, "phrases",
-exclusion, +required, OR, type:, filetype:, intitle:, before:, after:, regex, fuzzy,
title-weighted ranking, snippets, and deep links that scroll to the match.

    from shhkit import search
    idx = search.build_index(2416)              # live Canvas
    idx = search.index_from_mirror("~/courses/canvas/2416")
    search.run(idx, 'plagiatkontroll type:sida')
"""
import datetime
import html
import json
import os
import pathlib
import re
import urllib.parse

from . import canvas as canvas_api

TYPES = {
    "sida": "sida", "sidor": "sida", "page": "sida", "pages": "sida",
    "uppgift": "uppgift", "uppgifter": "uppgift", "assignment": "uppgift",
    "fil": "fil", "filer": "fil", "file": "fil",
    "modul": "modul", "moduler": "modul", "module": "modul",
    "diskussion": "diskussion", "discussion": "diskussion",
    "anvisning": "anvisning", "announcement": "anvisning",
}


def fold(s):
    return re.sub(r"\s+", " ", (s or "").lower().replace("å", "a").replace("ä", "a")
                  .replace("ö", "o").replace("é", "e")).strip()


def strip_html(h):
    """HTML -> text with block elements turned into "\\n".

    The newlines matter: a text-fragment anchor can never cross a block boundary, so the
    index has to remember where the page's paragraphs were. Without them "</p><p>" glues
    two sentences into "(OURGINAL)Observera" and every anchor spanning them fails.
    """
    s = re.sub(r"</(p|div|li|ul|ol|h[1-6]|tr|table|blockquote|section|article)>", "\n",
               h or "", flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


def norm_keep(s):
    """Lowercase + Swedish folding, but keeps "\\n" so block boundaries survive."""
    s = (s or "").lower().replace("å", "a").replace("ä", "a").replace("ö", "o").replace("é", "e")
    return re.sub(r"[ \t]+", " ", s)


def edit_distance(a, b, max_d=2):
    """Damerau-Levenshtein, bounded: returns max_d+1 when over the bound."""
    if abs(len(a) - len(b)) > max_d:
        return max_d + 1
    prev2 = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i]
        rowmin = i
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur.append(v)
            rowmin = min(rowmin, v)
        if rowmin > max_d:
            return max_d + 1
        prev2, prev = prev, cur
    return prev[len(b)]


def _words(text):
    return {w for w in re.split(r"[^a-z0-9]+", text) if len(w) > 2}


def _doc(type_, title, body, href, ext="", date=""):
    f = fold(f"{title} {body}")
    return {"type": type_, "title": title or "(utan titel)", "body": body or "",
            "href": href or "#", "ext": ext, "date": (date or "")[:10],
            "f": f, "w": sorted(_words(f))}


# ------------------------------------------------------------------ builders
def _adder(docs):
    """Return an add() that keeps the richest version of a document.

    A module item creates an empty stub for its assignment or page; the full document arrives
    later with the same key. First-write-wins would drop rubric text and task descriptions.
    """
    at = {}

    def add(type_, title, body, href, ext="", date=""):
        d = _doc(type_, title, body, href, ext=ext, date=date)
        key = (d["type"], d["title"], d["href"])
        if key not in at:
            at[key] = len(docs)
            docs.append(d)
            return
        pos = at[key]
        if len(d["body"]) + len(d["date"]) > len(docs[pos]["body"]) + len(docs[pos]["date"]):
            docs[pos] = d

    return add


def build_index(course_id, base=None):
    """Index a course from the live Canvas API. Read-only, token from $CANVAS_TOKEN."""
    docs = []
    add = _adder(docs)

    modules = canvas_api.get(f"courses/{course_id}/modules", **{"include[]": ["items"]})
    page_slugs = []
    for m in modules:
        for it in m.get("items", []):
            add("modul", f"{m['name']}: {it.get('title')}", m["name"], it.get("html_url") or it.get("url"))
            if it.get("type") == "Page":
                slug = it.get("page_url") or (it.get("url") or "").rsplit("/", 1)[-1]
                if slug:
                    page_slugs.append(slug)
            elif it.get("type") == "Assignment" and it.get("content_id"):
                add("uppgift", it.get("title"), "", f"/courses/{course_id}/assignments/{it['content_id']}")
            elif it.get("type") == "Discussion" and it.get("content_id"):
                add("diskussion", it.get("title"), "", f"/courses/{course_id}/discussion_topics/{it['content_id']}")

    for slug in dict.fromkeys(page_slugs):
        try:
            p = canvas_api.get(f"courses/{course_id}/pages/{urllib.parse.quote(slug)}")
        except Exception:
            continue
        add("sida", p.get("title") or slug, strip_html(p.get("body")), f"/courses/{course_id}/pages/{slug}",
            date=p.get("updated_at") or "")

    # include[]=rubric: without it Canvas returns rubric: [] and the grading criteria,
    # which students search for by name, never enter the index
    for a in canvas_api.try_get(f"courses/{course_id}/assignments", **{"include[]": ["rubric"]}) or []:
        if not isinstance(a, dict):
            continue
        rubric = " ".join(f"{r.get('description','')} {r.get('long_description','')}"
                          for r in (a.get("rubric") or []))
        add("uppgift", a.get("name"), f"{strip_html(a.get('description'))} {rubric}",
            f"/courses/{course_id}/assignments/{a.get('id')}", date=a.get("updated_at") or "")

    for d in canvas_api.try_get(f"courses/{course_id}/discussion_topics", only_announcements="true") or []:
        if isinstance(d, dict):
            add("anvisning", d.get("title"), strip_html(d.get("message")),
                f"/courses/{course_id}/discussion_topics/{d.get('id')}", date=d.get("posted_at") or "")
    for d in canvas_api.try_get(f"courses/{course_id}/discussion_topics") or []:
        if isinstance(d, dict) and not d.get("pinned"):
            add("diskussion", d.get("title"), strip_html(d.get("message")),
                f"/courses/{course_id}/discussion_topics/{d.get('id')}", date=d.get("posted_at") or "")

    for f_ in canvas_api.try_get(f"courses/{course_id}/files") or []:
        if isinstance(f_, dict):
            name = f_.get("display_name") or f_.get("filename") or ""
            add("fil", name, name, f_.get("url") or "", ext=(name.rsplit(".", 1)[-1] or "").lower(),
                date=f_.get("updated_at") or f_.get("created_at") or "")
    return {"course_id": course_id, "source": "api", **({"base": base} if base else {}), "docs": docs}


def index_from_mirror(path):
    """Index an existing `python -m shhkit mirror` output directory."""
    root = pathlib.Path(path).expanduser()
    docs = []
    add = _adder(docs)

    mods_file = root / "modules.json"
    course_id = root.name
    if mods_file.exists():
        for m in json.loads(mods_file.read_text()):
            for it in m.get("items", []):
                add("modul", f"{m['name']}: {it.get('title')}", m["name"], it.get("html_url") or it.get("url"))
                if it.get("type") == "Assignment" and it.get("content_id"):
                    add("uppgift", it.get("title"), "", f"/courses/{course_id}/assignments/{it['content_id']}")
                if it.get("type") == "Discussion" and it.get("content_id"):
                    add("diskussion", it.get("title"), "", f"/courses/{course_id}/discussion_topics/{it['content_id']}")

    for p in sorted((root / "pages").glob("*.html")) if (root / "pages").exists() else []:
        raw = p.read_text(errors="ignore")
        slug = re.sub(r"^\d+_\d+_", "", p.stem)
        title = next((m.group(1) for m in [re.search(r"title: ([^|]+)", raw[:400])] if m), slug)
        add("sida", title.strip(), strip_html(raw), f"/courses/{course_id}/pages/{slug}")

    for a in (json.loads((root / "assignments.json").read_text()) if (root / "assignments.json").exists() else []):
        if not isinstance(a, dict):
            continue
        rubric = " ".join(f"{r.get('description','')} {r.get('long_description','')}"
                          for r in (a.get("rubric") or []))
        add("uppgift", a.get("name"), f"{strip_html(a.get('description'))} {rubric}",
            f"/courses/{course_id}/assignments/{a.get('id')}", date=a.get("updated_at") or "")

    for name, kind in (("announcements.json", "anvisning"), ("discussions.json", "diskussion")):
        f = root / name
        for d in (json.loads(f.read_text()) if f.exists() else []):
            if isinstance(d, dict):
                add(kind, d.get("title"), strip_html(d.get("message")),
                    f"/courses/{course_id}/discussion_topics/{d.get('id')}", date=d.get("posted_at") or "")

    fl = root / "files.json"
    for f_ in (json.loads(fl.read_text()) if fl.exists() else []):
        if isinstance(f_, dict):
            name = f_.get("display_name") or f_.get("filename") or ""
            add("fil", name, name, f_.get("url") or "", ext=(name.rsplit(".", 1)[-1] or "").lower(),
                date=f_.get("updated_at") or "")
    return {"course_id": course_id, "source": "mirror", "docs": docs}


# ------------------------------------------------------------------ query
def parse_query(raw, case_sensitive=False, regex=False):
    q = {"terms": [], "not": [], "types": [], "exts": [], "title": [],
         "before": None, "after": None, "regex": None, "error": None}
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            q["regex"] = re.compile(raw, flags)
        except re.error as e:
            q["error"] = f"Ogiltigt reguljärt uttryck: {e}"
        return q
    for phrase, word in re.findall(r'"([^"]*)"|(\S+)', raw):
        is_phrase = phrase != "" or (phrase == "" and word == "")
        s = phrase if phrase else word
        if word:
            kv = re.match(r"^(type|filetype|intitle|before|after):(.+)$", word, re.I)
            if kv:
                k, v = kv.group(1).lower(), kv.group(2)
                if k == "intitle":
                    q["title"].append(v)
                    continue
                if k == "filetype":
                    q["exts"].append(v.lstrip(".").lower())
                    continue
                if k in ("before", "after"):
                    q[k] = _norm_date(v)
                    continue
                mapped = TYPES.get(v.lower())
                if mapped:
                    q["types"].append(mapped)
                else:
                    q["error"] = f"Okänd typ {v!r}. Använd: {', '.join(sorted(set(TYPES.values())))}"
                continue
            if word.startswith("-") and len(word) > 1:
                q["not"].append(word[1:])
                continue
            if word.startswith("+") and len(word) > 1:
                q["terms"].append(word[1:])
                continue
            if word.lower() in ("and", "or"):
                continue
        if s:
            q["terms"].append(s)
    return q


def _norm_date(v):
    m = re.match(r"^(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?$", str(v))
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2) if m.group(3) else '01'}"
    if re.match(r"^\d{4}$", str(v)):
        return f"{v}-01-01"
    return None


def _count_plain(hay, needle):
    if not needle:
        return 0
    return hay.count(needle)


def _count_word(hay, term):
    return len(re.findall(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", hay))


def _fuzzy_hits(doc, term):
    max_d = 1 if len(term) <= 5 else 2
    hits = []
    for w in doc["w"]:
        if w == term or w.startswith(term):
            hits.append(w)
        elif abs(len(w) - len(term)) <= max_d and edit_distance(term, w, max_d) <= max_d:
            hits.append(w)
    return hits


MIN_FRAGMENT = 12


def fragment_matches(anchor, title, body):
    """Would a browser text directive for this phrase actually find it?

    The phrase must occur inside a single block, so the comparison is line by line.
    """
    a = norm_keep(anchor or "").strip()
    if not a or len(a) < 8:
        return False
    lines = norm_keep(f"{title or ''}\n{body or ''}").split("\n")
    if any(a in ln for ln in lines):
        return True
    words = a.split(" ")
    return len(words) > 2 and any(" ".join(words[:3]) in ln for ln in lines)


def anchor_for(title, at, body):
    """Longest verified phrase at the match: whole words, max 8, inside one block.

    The offset is moved back to the start of its word, then forward to the next word,
    because a text directive that starts inside a word never matches.
    """
    b = body or ""
    is_w = lambda ch: bool(ch) and (ch.isalnum() or ch in "åäöé")
    i = max(0, min(at or 0, len(b) - 1)) if b else 0
    while i > 0 and is_w(b[i - 1]) and is_w(b[i]):
        i -= 1
    while i < len(b) and not is_w(b[i]):
        i += 1

    # work inside the block that contains the match
    line_start = b.rfind("\n", 0, max(0, i - 1)) + 1
    line_end = b.find("\n", i)
    if line_end < 0:
        line_end = len(b)
    line = re.sub(r"[ \t\u00a0]+", " ", b[line_start:line_end])

    words = [(m.group(0), m.start()) for m in re.finditer(r"[0-9a-zA-ZåäöÅÄÖé]+", line)]
    if not words:
        return ""
    k = next((n for n, (w, pos) in enumerate(words) if pos + len(w) > i - line_start), 0)

    for start in range(max(0, k - 3), k + 1):
        for n in range(min(8, len(words) - start), 0, -1):
            end = words[start + n - 1][1] + len(words[start + n - 1][0])
            cand = line[words[start][1]:end].strip()
            if len(cand) >= MIN_FRAGMENT and fragment_matches(cand, title, b):
                return cand
    return words[k][0] if fragment_matches(words[k][0], title, b) else ""


def anchor_target(doc, raw, regex=False, fuzzy=False, case_sensitive=False):
    body = doc.get("body") or ""
    seed = doc.get("anchor") or ""
    if not regex and not fuzzy:
        terms = sorted(parse_query(raw, case_sensitive)["terms"], key=len, reverse=True)
        if terms:
            seed = terms[0]
    at = doc.get("at") or 0
    if seed:
        # offsets must be measured in the original body: fold() collapses whitespace and shifts them
        pos = body.lower().find(str(seed).lower())
        if pos >= 0:
            at = pos
    return anchor_for(doc.get("title"), at, body)


def _match(doc, q, case_sensitive=False, fuzzy=False, whole_word=False):
    if q["types"] and doc["type"] not in q["types"]:
        return None
    if q["exts"] and doc["ext"] not in q["exts"]:
        return None
    if q["before"] or q["after"]:
        d = doc["date"][:10]
        if not d:
            return None
        if q["before"] and not d < q["before"]:
            return None
        if q["after"] and not d > q["after"]:
            return None

    if q["regex"]:
        hay = f"{doc['title']}\n{doc['body']}"
        m = list(q["regex"].finditer(hay))
        if not m:
            return None
        return {"score": len(m) + 5, "at": m[0].start(), "anchor": m[0].group(0)}

    prep = (lambda s: (s or "")) if case_sensitive else fold
    hay_src, title_src = f"{doc['title']}\n{doc['body']}", doc["title"]
    hay, title = prep(hay_src), prep(title_src)
    if any(prep(n) in hay for n in q["not"]):
        return None
    if any(prep(t) not in title for t in q["title"]):
        return None

    score, at, best = 0, -1, ""
    for term in q["terms"]:
        t = prep(term)
        if fuzzy:
            fw = _fuzzy_hits(doc, t)
            c, anchor = len(fw), (fw[0] if fw else t)
        elif whole_word or len(t) <= 3:
            c, anchor = _count_word(hay, t), t
        else:
            c, anchor = _count_plain(hay, t), t
        if not c:
            return None
        if at < 0:
            pos = hay.find(anchor)
            at = pos if pos >= 0 else 0
        best = best if len(best) > len(anchor) else anchor
        score += (12 if anchor in title else 0) + min(c, 6)
    if doc["type"] in ("anvisning", "sida"):
        score += 3
    return {"score": score, "at": at, "anchor": best}


def run(index, raw, case_sensitive=False, regex=False, fuzzy=False, whole_word=False, limit=40):
    """Search an index. Returns {'hits': [...], 'total': n} or {'error': '...'}."""
    alts = [raw] if regex else (re.split(r"\s+OR\s+", raw, flags=re.I) if re.search(r"\s+OR\s+", raw, flags=re.I) else [raw])
    found, constrained = {}, False
    for alt in alts:
        q = parse_query(alt, case_sensitive, regex)
        if q["error"]:
            return {"error": q["error"], "hits": [], "total": 0}
        if not (q["terms"] or q["regex"] or q["title"] or q["types"] or q["exts"] or q["before"] or q["after"]):
            continue
        constrained = True
        for doc in index["docs"]:
            r = _match(doc, q, case_sensitive, fuzzy, whole_word)
            if not r:
                continue
            key = (doc["href"], doc["title"])
            if key not in found or r["score"] > found[key]["score"]:
                found[key] = {**doc, "score": r["score"], "at": r["at"]}
            cur = found[key]
            if r.get("anchor") and len(r["anchor"]) > len(cur.get("anchor") or ""):
                cur["anchor"] = r["anchor"]
    if not constrained:
        return {"hits": [], "total": 0, "empty": True}
    hits = sorted(found.values(), key=lambda d: -d["score"])[:limit]
    for h in hits:
        h["snippet"] = snippet(h, h["at"])
        h["anchor"] = anchor_target(h, raw, regex=regex, fuzzy=fuzzy, case_sensitive=case_sensitive)
        # an unverifiable anchor yields the bare link rather than a dead fragment
        h["url"] = deep_link(h["href"], h["anchor"])
    return {"hits": hits, "total": len(found)}


def suggest(index, raw):
    q = parse_query(raw)
    if not q["terms"]:
        return None
    term = fold(q["terms"][0])
    if len(term) < 4:
        return None
    best = None
    seen = set()
    for doc in index["docs"]:
        for w in doc["w"]:
            if w in seen:
                continue
            seen.add(w)
            if len(w) < 4:
                continue
            d = edit_distance(term, w, 2)
            if d == 0 or d > 2:
                continue
            if best is None or d < best[0] or (d == best[0] and len(w) < len(best[1])):
                best = (d, w)
    return best[1] if best else None


def snippet(doc, at, width=230):
    body = doc["body"] if len(doc["body"] or "") > 20 else doc["title"]
    start = max(0, (at or 0) - width // 3)
    return ("…" if start else "") + re.sub(r"\s+", " ", body[start:start + width])


def deep_link(href, phrase):
    if not href or not phrase:
        return href
    clean = re.sub(r"[\r\n]+", " ", phrase).strip()[:120]
    enc = urllib.parse.quote(clean, safe="").replace("-", "%2D").replace(",", "%2C")
    return f"{href}{'&' if '#' in href else '#:~:'}text={enc}"
