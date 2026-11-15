"""Ad first cut: script formats, shot/take matching, dialogue alignment, assembly rules."""

import csv
import os
from fractions import Fraction

import pytest

from engine.ad import (AdSettings, Clip, align_line, build_ad_plan, name_shot_take, norm_words, read_takelog, shot_key,
                       validate_ad_plan)
from engine.script import ScriptError, parse_script
from engine.stt import Transcript, Word

FIX = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "scripts")
FPS = Fraction(25)


# ------------------------------------------------------------------ scripts

def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return str(path)


def test_csv_shot_list_with_synonym_headers(tmp_path):
    p = write_csv(tmp_path / "s.csv", [
        ["Shot #", "Video", "Audio", "Dur"],
        ["1", "Wide kitchen", "", "3"],
        ["2", "Close Sarah", "SARAH: Fresh coffee in 60 seconds.", "2.5"],
        ["3", "Packshot", "VO: Aurora. Wake up better.", "0:04"],
    ])
    sc = parse_script(p)
    assert [s.shot_id for s in sc.shots] == ["1", "2", "3"]
    assert [s.kind for s in sc.shots] == ["mos", "dialogue", "vo"]
    assert sc.shots[1].speaker == "SARAH" and sc.shots[1].dialogue == "Fresh coffee in 60 seconds."
    assert sc.shots[2].target_s == 4.0


def test_xlsx_docx_fdx_txt(tmp_path):
    from docx import Document
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for row in [["Order", "Shot", "Description", "Dialogue", "Type"], [2, "B", "Second", "", "MOS"], [1, "A", "First", "Hi there", "dialogue"]]:
        ws.append(row)
    wb.save(tmp_path / "s.xlsx")
    sc = parse_script(str(tmp_path / "s.xlsx"))
    assert [s.shot_id for s in sc.shots] == ["A", "B"], "rows follow the order column"

    d = Document()
    t = d.add_table(rows=3, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "VIDEO", "AUDIO"
    t.cell(1, 0).text, t.cell(1, 1).text = "Open on the city", "VO: Every morning counts."
    t.cell(2, 0).text, t.cell(2, 1).text = "Sarah smiles", "SARAH: Let's go."
    d.save(tmp_path / "av.docx")
    sc = parse_script(str(tmp_path / "av.docx"))
    assert [s.kind for s in sc.shots] == ["vo", "dialogue"] and sc.shots[1].dialogue == "Let's go."

    fdx = tmp_path / "s.fdx"
    fdx.write_text('<?xml version="1.0"?><FinalDraft><Content>'
                   '<Paragraph Type="Scene Heading" Number="1"><Text>INT. KITCHEN - DAY</Text></Paragraph>'
                   '<Paragraph Type="Action"><Text>Sarah walks in.</Text></Paragraph>'
                   '<Paragraph Type="Character"><Text>SARAH</Text></Paragraph>'
                   '<Paragraph Type="Dialogue"><Text>Coffee time.</Text></Paragraph>'
                   '<Paragraph Type="Scene Heading" Number="2"><Text>EXT. STREET</Text></Paragraph>'
                   '<Paragraph Type="Action"><Text>She runs.</Text></Paragraph>'
                   '</Content></FinalDraft>', encoding="utf-8")
    sc = parse_script(str(fdx))
    assert [s.shot_id for s in sc.shots] == ["1", "2"]
    assert sc.shots[0].dialogue == "Coffee time." and sc.shots[1].kind == "mos"

    txt = tmp_path / "s.txt"
    txt.write_text("Shot 1: Wide kitchen\n\nShot 2: Close on Sarah\nSARAH: Morning!\n", encoding="utf-8")
    sc = parse_script(str(txt))
    assert [(s.shot_id, s.kind) for s in sc.shots] == [("1", "mos"), ("2", "dialogue")]


def test_storyboard_pdf_panels_and_pictures(tmp_path):
    sc = parse_script(os.path.join(FIX, "storyboard.pdf"), board_dir=str(tmp_path / "boards"))
    assert [s.shot_id for s in sc.shots] == ["1", "2", "3"]
    assert sc.shots[1].kind == "dialogue" and "sixty seconds" in sc.shots[1].dialogue
    assert sc.shots[2].kind == "vo"
    assert all(s.board and os.path.isfile(s.board) for s in sc.shots)


def test_bad_scripts_are_refused_with_a_reason(tmp_path):
    with pytest.raises(ScriptError, match="description"):
        parse_script(write_csv(tmp_path / "x.csv", [["foo", "bar"], ["1", "2"]]))
    with pytest.raises(ScriptError, match="unsupported"):
        (tmp_path / "x.pages").write_text("x")
        parse_script(str(tmp_path / "x.pages"))


# ------------------------------------------------------------------ matching pieces

@pytest.mark.parametrize("name,want", [
    ("03A_T2.mov", ("3a", 2)), ("SH03A-TK02.mp4", ("3a", 2)), ("shot 12 take 3.mov", ("12", 3)),
    ("S03_SH02_T04.mov", ("3-2", 4)), ("3A-2.mov", ("3a", 2)), ("A001C004_221114_R1AB.mov", None),
])
def test_file_names(name, want):
    got = name_shot_take(name)
    assert (got[0] if got else None) == want


def test_shot_keys_and_words():
    assert shot_key("Shot 03A") == "3a" and shot_key("3.2") == "3-2" and shot_key("SC3 SH2") == "3-2"
    assert norm_words("Fresh coffee in 60 seconds!") == ["fresh", "coffee", "in", "sixty", "seconds"]


def test_takelog(tmp_path):
    p = write_csv(tmp_path / "log.csv", [["Clip", "Shot", "Take", "Circled", "Notes"],
                                        ["A001C004.mov", "2", "3", "yes", "best smile"], ["A001C005", "2", "4", "", ""]])
    log = read_takelog(p)
    assert log["a001c004"].shot == "2" and log["a001c004"].circled and log["a001c004"].take == 3
    assert not log["a001c005"].circled


def words(text, start=0.0, step=0.4):
    return [Word(text=w, start=round(start + i * step, 3), end=round(start + i * step + 0.3, 3)) for i, w in enumerate(text.split())]


def test_align_finds_each_delivery_of_a_line():
    t = Transcript(clip_id="c", model="t", words=words("uh okay fresh coffee in 60 seconds cut fresh coffee in sixty seconds", 1.0))
    ms = align_line("Fresh coffee in sixty seconds.", t)
    assert len(ms) == 2 and all(m.score >= 90 for m in ms)
    assert sorted(round(m.start, 2) for m in ms) == [1.8, 4.2]
    assert align_line("Totally different words here", t) == []


# ------------------------------------------------------------------ assembly

def script_csv(tmp_path):
    return parse_script(write_csv(tmp_path / "ad.csv", [
        ["Shot", "Video", "Audio", "Duration"],
        ["1", "Wide kitchen", "", "3"],
        ["2", "Close Sarah", "SARAH: Fresh coffee in sixty seconds.", ""],
        ["3", "Pour", "", "2"],
        ["4", "Packshot", "VO: Aurora. Wake up better.", "3"],
        ["5", "Missing shot", "", "2"],
    ]))


def clips():
    return [Clip("c1", "/s/01_T1.mov", "01_T1.mov", 8.0), Clip("c1b", "/s/01_T2.mov", "01_T2.mov", 8.0),
            Clip("c2a", "/s/A001C010.mov", "A001C010.mov", 9.0), Clip("c2b", "/s/A001C011.mov", "A001C011.mov", 9.0),
            Clip("c3", "/s/03_T1.mov", "03_T1.mov", 6.0), Clip("c4", "/s/04_T1.mov", "04_T1.mov", 6.0)]


def test_plan_in_script_order_with_dialogue_trim_missing_and_circled(tmp_path, monkeypatch):
    sc = script_csv(tmp_path)
    tr = {"c2a": Transcript(clip_id="c2a", model="t", words=words("rolling fresh coffee in sixty seconds", 2.0)),
          "c2b": Transcript(clip_id="c2b", model="t", words=words("fresh coffee in sixty seconds", 3.0))}
    log = {"a001c011": __import__("engine.ad", fromlist=["LogEntry"]).LogEntry("2", 5, True, "circled")}
    plan = build_ad_plan(sc, clips(), FPS, AdSettings(), transcripts=tr, takelog=log)
    assert validate_ad_plan(plan, clips()) == []
    s = {a.shot_id: a for a in plan.shots}
    assert [a.shot_id for a in plan.shots] == ["1", "2", "3", "4", "5"]
    assert s["1"].name == "01_T2.mov", "later take wins when nothing else decides"
    assert s["2"].name == "A001C011.mov" and s["2"].circled and s["2"].method == "take log"
    assert s["2"].line_score >= 95
    assert abs(s["2"].src_in_s - (3.0 - 0.25)) < 0.05, "dialogue trimmed to the line with a handle"
    assert s["5"].status == "missing" and plan.stats["missing"] == ["5"]
    assert s["4"].kind == "vo" and s["4"].status == "matched"
    assert (s["1"].end - s["1"].start) == 75, "3 s at 25 fps from the script"


def test_target_length_fits_by_adjusting_only_mos_shots(tmp_path):
    sc = script_csv(tmp_path)
    tr = {"c2b": Transcript(clip_id="c2b", model="t", words=words("fresh coffee in sixty seconds", 3.0))}
    free = build_ad_plan(sc, clips(), FPS, AdSettings(), transcripts=tr)
    line = next(a for a in free.shots if a.shot_id == "2")
    fit = build_ad_plan(sc, clips(), FPS, AdSettings(target_s=10.0), transcripts=tr)
    assert validate_ad_plan(fit, clips()) == []
    assert fit.duration_frames == 250, "a target length is met to the frame"
    line2 = next(a for a in fit.shots if a.shot_id == "2")
    assert line2.end - line2.start == line.end - line.start, "the spoken line is never shortened"
    tight = build_ad_plan(sc, clips(), FPS, AdSettings(target_s=3.0), transcripts=tr)
    assert any("target" in w for w in tight.warnings), "an impossible target is reported, not faked"


def test_beats_move_only_adjustable_cuts_and_keep_the_length(tmp_path):
    sc = script_csv(tmp_path)
    plan0 = build_ad_plan(sc, clips(), FPS, AdSettings(snap_to_beats=True))
    beats = [i * 0.5 + 0.1 for i in range(60)]
    plan = build_ad_plan(sc, clips(), FPS, AdSettings(snap_to_beats=True), beats=beats)
    assert plan.duration_frames == plan0.duration_frames
    assert validate_ad_plan(plan, clips()) == []
    beat_f = {round(b * 25) for b in beats}
    moved = [a.start for a in plan.shots[1:] if a.start in beat_f]
    assert moved, "at least one cut landed on a beat"


def test_line_is_pulled_onto_measured_speech():
    """Whisper's first word starts at 0.0 over silence and the last word runs to the end of the
    audio; the measured speech regions put the line where it is really spoken."""
    from engine.ad import refine_to_speech
    w = [Word(text="it", start=0.0, end=0.28), Word(text="tastes", start=0.28, end=1.5), Word(text="like", start=1.5, end=1.71),
         Word(text="a", start=1.71, end=1.85), Word(text="holiday", start=1.85, end=7.0)]
    t = Transcript(clip_id="c", model="t", words=w, speech=[[1.52, 3.31]])
    m = align_line("It tastes like a holiday.", t)[0]
    assert abs(m.start - 1.52) < 0.01 and abs(m.end - 3.31) < 0.01
    # neighbouring speech after the line (a crew call) is not swallowed
    assert refine_to_speech(1.4, 6.0, [[1.5, 3.0], [4.5, 5.5]], 1.9, 2.8) == (1.5, 3.0)
    assert refine_to_speech(1.0, 2.0, []) == (1.0, 2.0)


def test_matching_in_other_languages():
    from engine.ad import norm_words
    from engine.stt import model_for
    assert norm_words("¡Café recién hecho en 60 segundos!", "es") == ["cafe", "recien", "hecho", "en", "60", "segundos"]
    assert norm_words("Café crème", "fr") == ["cafe", "creme"]
    assert norm_words("ताज़ा कॉफ़ी, साठ सेकंड में।", "hi") == ["ताज़ा", "कॉफ़ी", "साठ", "सेकंड", "में"]
    assert norm_words("Свежий кофе за 60 секунд", "ru")[:2] == ["свежий", "кофе"]
    t = Transcript(clip_id="c", model="base", words=words("बस ताज़ा कॉफ़ी साठ सेकंड में", 1.0))
    ms = align_line("ताज़ा कॉफ़ी, साठ सेकंड में।", t, language="hi")
    assert ms and ms[0].score >= 95
    assert model_for("en") == "base.en" and model_for("hi") == "base" and model_for("auto", "small") == "small"


def test_cutdowns_from_script_columns_and_automatic(tmp_path):
    from engine.ad import cutdown_script
    p = write_csv(tmp_path / "v.csv", [
        ["Shot", "Type", "Video", "Audio", "Duration", "in 15", "in 6"],
        ["1", "mos", "Opening wide", "", "3", "x", ""],
        ["2", "dialogue", "Close Sarah", "SARAH: Fresh coffee in sixty seconds.", "", "x", "x"],
        ["3", "mos", "Pour", "", "2", "", ""],
        ["4", "vo", "Packshot", "VO: Aurora. Wake up better.", "3", "x", "x"],
    ])
    sc = parse_script(p)
    assert [s.versions for s in sc.shots] == [[15], [6, 15], [], [6, 15]]
    tr = {"c2b": Transcript(clip_id="c2b", model="t", words=words("fresh coffee in sixty seconds", 3.0))}
    full = build_ad_plan(sc, clips(), FPS, AdSettings(), transcripts=tr)
    sub15, dropped15, how15 = cutdown_script(sc, 15, full, FPS)
    assert [s.shot_id for s in sub15.shots] == ["1", "2", "4"] and dropped15 == ["3"] and "script" in how15
    sub6, _, _ = cutdown_script(sc, 6, full, FPS)
    assert [s.shot_id for s in sub6.shots] == ["2", "4"]
    # Without columns: the packshot, the lines and the opening first; picture shots while they fit
    plain = parse_script(write_csv(tmp_path / "p.csv", [r[:5] for r in [
        ["Shot", "Type", "Video", "Audio", "Duration"], ["1", "mos", "Opening wide", "", "3"],
        ["2", "dialogue", "Close Sarah", "SARAH: Fresh coffee in sixty seconds.", ""], ["3", "mos", "Pour", "", "2"],
        ["4", "vo", "Packshot", "VO: Aurora. Wake up better.", "3"]]]))
    fullp = build_ad_plan(plain, clips(), FPS, AdSettings(), transcripts=tr)
    sub, dropped, how = cutdown_script(plain, 4.0, fullp, FPS)
    assert "4" in [s.shot_id for s in sub.shots] and "automatic" in how
    plan = build_ad_plan(sub, clips(), FPS, AdSettings(target_s=4.0), transcripts=tr)
    assert validate_ad_plan(plan, clips()) == []
