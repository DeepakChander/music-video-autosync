"""Read an ad script or storyboard into an ordered list of shots.

Formats (docs/AD_GUIDE.md has the template):
  * .csv / .xlsx shot list: one row per shot. Recognised headers (case-insensitive, synonyms):
    order, shot (shot id / shot # / scene), description (video / picture / action / visual),
    dialogue (audio / vo / voiceover / line / copy), speaker (character / talent), type
    (dialogue / vo / mos / graphic), duration (dur / seconds / length / target), board (image /
    frame / storyboard), notes, super (text / title).
  * .docx AV script: a table whose header names a video column and an audio column (two-column
    AV script, StudioBinder template); otherwise the first table's first two columns.
  * .fdx (Final Draft): Scene Heading starts a shot; Action -> description; Character + Dialogue.
  * .txt: blocks separated by blank lines; "NAME: line" or "VO: line" lines are dialogue.
  * .pdf storyboard: text per page split at "Shot 3", "Frame 3", "Panel 3", "Scene 3" or "3." /
    "3A." headings; embedded images become the panels' board pictures, in page order.
Nothing is invented: a shot without dialogue is MOS unless the script says VO.
"""

from __future__ import annotations

import csv
import os
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ScriptShot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: int
    shot_id: str
    description: str = ""
    dialogue: str = ""
    speaker: str = ""
    kind: str = "mos"            # dialogue | vo | mos | graphic
    target_s: Optional[float] = None
    board: Optional[str] = None  # storyboard image path
    notes: str = ""
    super_text: str = ""
    versions: list[int] = Field(default_factory=list)   # cutdown lengths this shot belongs to (script columns "in 15", "in 6" ...)


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    format: str
    shots: list[ScriptShot]
    warnings: list[str] = Field(default_factory=list)


class ScriptError(ValueError):
    pass


_SYN = {
    "order": ["order", "#", "no", "no.", "number", "seq", "sequence"],
    "shot_id": ["shot", "shot id", "shot_id", "shot #", "shot no", "shot number", "scene", "id", "frame", "panel"],
    "description": ["description", "video", "picture", "action", "visual", "visuals", "shot description", "what we see"],
    "dialogue": ["dialogue", "dialog", "audio", "vo", "v/o", "voiceover", "voice over", "line", "lines", "copy", "script"],
    "speaker": ["speaker", "character", "talent", "who"],
    "kind": ["type", "kind", "audio type"],
    "target_s": ["duration", "dur", "seconds", "secs", "length", "target", "time", "target_dur_s", "target duration"],
    "board": ["board", "image", "frame image", "storyboard", "board_ref", "picture file"],
    "notes": ["notes", "note", "comments"],
    "super_text": ["super", "supers", "text", "title", "on screen text", "graphics"],
}


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", str(h or "").strip().lower())


def _map_headers(headers: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    norm = [_norm_header(h) for h in headers]
    for field, names in _SYN.items():
        for i, h in enumerate(norm):
            if h in names and field not in out and i not in out.values():
                out[field] = i
                break
    return out


def _duration(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().lower().replace("sec", "").replace("s", "").strip()
    if not s:
        return None
    m = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", s)
    try:
        return float(m.group(1)) * 60 + float(m.group(2)) if m else float(s)
    except ValueError:
        return None


def _kind(raw: str, dialogue: str, speaker: str) -> str:
    r = (raw or "").strip().lower()
    if r in ("dialogue", "dialog", "sync", "sot"):
        return "dialogue"
    if r in ("vo", "v/o", "voiceover", "voice over"):
        return "vo"
    if r in ("mos", "b-roll", "broll", "picture", "visual"):
        return "mos"
    if r in ("graphic", "graphics", "endcard", "end card", "packshot", "super"):
        return "graphic"
    if not dialogue.strip():
        return "mos"
    sp = (speaker or "").strip().lower()
    if sp in ("vo", "v/o", "voiceover", "voice over", "narrator", "announcer") or re.match(r"^\s*(vo|v/o)\s*[:\-]", dialogue, re.I):
        return "vo"
    return "dialogue"


def _strip_speaker(dialogue: str, speaker: str) -> tuple[str, str]:
    m = re.match(r"^\s*([A-Z][A-Z0-9 .'/-]{1,30})\s*[:\-]\s*(.+)$", dialogue.strip(), re.S)
    if m and not speaker:
        return m.group(2).strip(), m.group(1).strip()
    return dialogue.strip(), speaker


def _rows_to_shots(rows: list[list[str]], base_dir: str, warnings: list[str]) -> list[ScriptShot]:
    if not rows:
        return []
    hmap = _map_headers(rows[0])
    if "description" not in hmap and "dialogue" not in hmap:
        raise ScriptError("the shot list needs a description (video) or a dialogue (audio) column; "
                          "headers found: " + ", ".join(str(h) for h in rows[0]))
    # cutdown columns: "in 15", "in_15", "in15s", "15s version" ... -> {column: length}
    vcols = {}
    for i, h in enumerate(rows[0]):
        m = re.match(r"^\s*(?:in[ _-]?)?(\d{1,3})\s*s?(?:ec)?(?:\s*(?:version|cut|cutdown))?\s*$", str(h or "").strip().lower())
        if m and i not in hmap.values() and str(h).strip().lower() not in ("order", "#"):
            vcols[i] = int(m.group(1))
    yes = {"y", "yes", "x", "1", "true", "✓", "✔", "●", "in"}
    shots = []
    for r in rows[1:]:
        cell = lambda f: (str(r[hmap[f]]).strip() if f in hmap and hmap[f] < len(r) and r[hmap[f]] is not None else "")  # noqa: E731
        desc, dia = cell("description"), cell("dialogue")
        if not desc and not dia and not cell("shot_id"):
            continue
        dia, speaker = _strip_speaker(dia, cell("speaker"))
        board = cell("board") or None
        if board and not os.path.isabs(board):
            board = os.path.normpath(os.path.join(base_dir, board))
        shots.append(ScriptShot(order=len(shots) + 1, shot_id=cell("shot_id") or str(len(shots) + 1),
                                description=desc, dialogue=dia, speaker=speaker,
                                kind=_kind(cell("kind"), dia, speaker), target_s=_duration(cell("target_s")),
                                board=board, notes=cell("notes"), super_text=cell("super_text"),
                                versions=sorted(L for i, L in vcols.items() if i < len(r) and str(r[i] or "").strip().lower() in yes)))
    if "order" in hmap:
        def key(s):
            try:
                return float(rows[s.order][hmap["order"]])
            except (ValueError, TypeError, IndexError):
                return float(s.order)
        shots.sort(key=key)
        for i, s in enumerate(shots, start=1):
            s.order = i
    return shots


def _read_csv(path: str) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [row for row in csv.reader(f, dialect)]


def _read_xlsx(path: str) -> list[list[str]]:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
    while rows and not any(c.strip() for c in rows[0]):
        rows.pop(0)
    return rows


def _read_docx(path: str, warnings: list[str]) -> list[ScriptShot]:
    from docx import Document
    doc = Document(path)
    for t in doc.tables:
        rows = [[c.text.strip() for c in row.cells] for row in t.rows]
        if len(rows) < 2 or len(rows[0]) < 2:
            continue
        hmap = _map_headers(rows[0])
        if "description" in hmap or "dialogue" in hmap:
            return _rows_to_shots(rows, os.path.dirname(path), warnings)
        # Two-column AV script without recognised headers: VIDEO | AUDIO
        warnings.append("Table has no recognised headers: column 1 read as video, column 2 as audio.")
        return _rows_to_shots([["description", "dialogue"]] + [r[:2] for r in rows], os.path.dirname(path), warnings)
    text = "\n".join(p.text for p in doc.paragraphs)
    warnings.append("No table found: paragraphs read as a plain-text script.")
    return _text_to_shots(text)


def _text_to_shots(text: str) -> list[ScriptShot]:
    shots = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        shot_id = None
        m = re.match(r"^(?:shot|scene|frame|panel|sc|sh)\s*#?\s*([0-9]+[A-Za-z]?)\b[\s:.\-]*(.*)$", lines[0], re.I)
        if m:
            shot_id = m.group(1)
            lines[0] = m.group(2)
        desc, dia, speaker = [], [], ""
        for ln in lines:
            d = re.match(r"^([A-Z][A-Z0-9 .'/()-]{1,30}):\s*(.+)$", ln)
            if d:
                speaker = speaker or d.group(1).strip()
                dia.append(d.group(2).strip())
            elif ln:
                desc.append(ln)
        dialogue = " ".join(dia)
        shots.append(ScriptShot(order=len(shots) + 1, shot_id=shot_id or str(len(shots) + 1), description=" ".join(desc),
                                dialogue=dialogue, speaker=speaker, kind=_kind("", dialogue, speaker)))
    return shots


def _read_fdx(path: str) -> list[ScriptShot]:
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    shots: list[ScriptShot] = []
    cur: Optional[dict] = None
    speaker = ""

    def flush():
        if cur and (cur["desc"] or cur["dia"]):
            dialogue = " ".join(cur["dia"])
            shots.append(ScriptShot(order=len(shots) + 1, shot_id=cur["id"] or str(len(shots) + 1), description=" ".join(cur["desc"]),
                                    dialogue=dialogue, speaker=cur["speaker"], kind=_kind("", dialogue, cur["speaker"])))

    for p in root.iter("Paragraph"):
        typ = p.get("Type", "")
        text = "".join(t.text or "" for t in p.iter("Text")).strip()
        if typ == "Scene Heading":
            flush()
            cur = {"id": p.get("Number") or "", "desc": [text] if text else [], "dia": [], "speaker": ""}
            continue
        if cur is None:
            cur = {"id": "", "desc": [], "dia": [], "speaker": ""}
        if typ == "Action" and text:
            cur["desc"].append(text)
        elif typ == "Character":
            speaker = re.sub(r"\s*\((V\.?O\.?|O\.?S\.?|CONT'D)\)\s*", lambda m: " (VO)" if "V" in m.group(1).upper() else "", text).strip()
            cur["speaker"] = cur["speaker"] or speaker
        elif typ == "Dialogue" and text:
            cur["dia"].append(text)
    flush()
    return shots


def _read_pdf(path: str, out_dir: Optional[str], warnings: list[str]) -> list[ScriptShot]:
    from pypdf import PdfReader
    reader = PdfReader(path)
    head = re.compile(r"(?im)^\s*(?:(?:shot|frame|panel|scene|sc)\s*#?\s*([0-9]+[A-Za-z]?)|([0-9]+[A-Za-z]?)[.)])\s+")
    shots: list[ScriptShot] = []
    images: list[str] = []
    for pi, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        marks = list(head.finditer(text))
        for k, m in enumerate(marks):
            body = text[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(text)].strip()
            shot_id = m.group(1) or m.group(2)
            sub = _text_to_shots(body.replace("\n", "\n\n") if not re.search(r"\n\s*\n", body) else body)
            desc = " ".join(s.description for s in sub).strip()
            dia = " ".join(s.dialogue for s in sub).strip()
            spk = next((s.speaker for s in sub if s.speaker), "")
            shots.append(ScriptShot(order=len(shots) + 1, shot_id=shot_id, description=desc, dialogue=dia,
                                    speaker=spk, kind=_kind("", dia, spk)))
        if out_dir:
            try:
                for ii, img in enumerate(page.images):
                    os.makedirs(out_dir, exist_ok=True)
                    p = os.path.join(out_dir, f"board_p{pi + 1:02d}_{ii + 1:02d}{os.path.splitext(img.name)[1] or '.png'}")
                    with open(p, "wb") as f:
                        f.write(img.data)
                    images.append(p)
            except Exception as e:  # image extraction is best effort; reported, never fatal
                warnings.append(f"page {pi + 1}: images not extracted ({type(e).__name__})")
    if not shots:
        raise ScriptError("No shots found in the PDF (looked for 'Shot 1', 'Frame 1', 'Panel 1', '1.' headings). "
                          "Export the storyboard as a shot list (CSV/XLSX) instead.")
    if images and len(images) == len(shots):
        for s, p in zip(shots, images):
            s.board = p
    elif images:
        warnings.append(f"{len(images)} images for {len(shots)} shots: board pictures not assigned (counts differ).")
    warnings.append("PDF text extraction is approximate: check the shot list before building the cut.")
    return shots


def parse_script(path: str, board_dir: Optional[str] = None) -> Script:
    if not os.path.isfile(path):
        raise ScriptError(f"script not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    warnings: list[str] = []
    if ext in (".csv", ".tsv"):
        shots = _rows_to_shots(_read_csv(path), os.path.dirname(path), warnings)
    elif ext in (".xlsx", ".xlsm"):
        shots = _rows_to_shots(_read_xlsx(path), os.path.dirname(path), warnings)
    elif ext == ".docx":
        shots = _read_docx(path, warnings)
    elif ext == ".fdx":
        shots = _read_fdx(path)
    elif ext in (".txt", ".text", ".fountain", ".md"):
        with open(path, encoding="utf-8-sig") as f:
            shots = _text_to_shots(f.read())
    elif ext == ".pdf":
        shots = _read_pdf(path, board_dir, warnings)
    else:
        raise ScriptError(f"unsupported script format {ext}: use .csv, .xlsx, .docx, .fdx, .txt or .pdf")
    if not shots:
        raise ScriptError("the script has no shots")
    seen: dict[str, int] = {}
    for s in shots:  # shot ids must be unique: repeat ids get a suffix and a warning
        k = s.shot_id.upper()
        if k in seen:
            seen[k] += 1
            warnings.append(f"shot id {s.shot_id} appears more than once; renamed {s.shot_id}-{seen[k]}")
            s.shot_id = f"{s.shot_id}-{seen[k]}"
        else:
            seen[k] = 1
    return Script(source=os.path.abspath(path), format=ext.lstrip("."), shots=shots, warnings=warnings)


TEMPLATE_ROWS = [
    ["order", "shot", "type", "description", "dialogue", "speaker", "duration", "board", "super", "notes", "in 15", "in 6"],
    ["1", "1A", "mos", "Wide: kitchen at sunrise", "", "", "3", "", "", "", "x", ""],
    ["2", "2A", "dialogue", "Close: Sarah at the coffee maker", "Fresh coffee in sixty seconds.", "SARAH", "", "", "", "", "x", "x"],
    ["3", "3A", "mos", "Insert: coffee pouring", "", "", "2", "", "", "", "", ""],
    ["4", "4A", "vo", "Packshot on the counter", "Aurora. Wake up better.", "VO", "3", "", "AURORA - aurora.example", "", "x", "x"],
]


def write_template(path: str) -> str:
    """The shot-list template (docs/AD_GUIDE.md): type = dialogue | vo | mos | graphic; duration in
    seconds for shots without dialogue; board = storyboard image file (optional)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(TEMPLATE_ROWS)
    return os.path.abspath(path)
