from __future__ import annotations

from pathlib import Path

from cinesub.core.models import VideoMetadata
from cinesub.core.utils import (
    decode_and_normalize_subtitle_content,
    parse_video_metadata,
    score_subtitle_candidate,
)


def test_obscure_movies_metadata_parsing(tmp_path: Path) -> None:
    cases = [
        {
            "filename": (
                "Jeanne.Dielman.23.quai.du.Commerce.1080.Bruxelles.1975.CRITERION.1080p.BluRay.x264-EA.mkv"
            ),
            "expected_year": 1975,
            "expected_screen_size": "1080p",
            "expected_group": "EA",
            "is_episode": False,
        },
        {
            "filename": "1917.2019.1080p.UHD.BluRay.x265-TERMiNAL.mkv",
            "expected_title": "1917",
            "expected_year": 2019,
            "expected_screen_size": "1080p",
            "expected_group": "TERMiNAL",
            "is_episode": False,
        },
        {
            "filename": "2001.A.Space.Odyssey.1968.REMASTERED.1080p.BluRay.x264-DEPTH.mkv",
            "expected_year": 1968,
            "expected_screen_size": "1080p",
            "expected_group": "DEPTH",
            "is_episode": False,
        },
        {
            "filename": (
                "Tetsuo.The.Iron.Man.1989.JAPANESE.REMASTERED.1080p.BluRay.x265-HANDJOB.mkv"
            ),
            "expected_year": 1989,
            "expected_screen_size": "1080p",
            "expected_group": "HANDJOB",
            "is_episode": False,
        },
        {
            "filename": "Moartea.Domnului.Lazarescu.2005.RO.BDRip.x264-ROSub.mkv",
            "expected_year": 2005,
            "expected_group": "ROSub",
            "is_episode": False,
        },
        {
            "filename": "Werckmeister.harmoniak.2000.HUNGARIAN.1080p.BluRay.x264-GHOULS.mkv",
            "expected_year": 2000,
            "expected_screen_size": "1080p",
            "expected_group": "GHOULS",
            "is_episode": False,
        },
        {
            "filename": "Stalker.1979.CRITERION.1080p.BluRay.x264-EA.mkv",
            "expected_year": 1979,
            "expected_screen_size": "1080p",
            "expected_group": "EA",
            "is_episode": False,
        },
        {
            "filename": "La.Casa.Lobo.2018.SPANISH.1080p.WEB-DL.H.264-KG.mkv",
            "expected_year": 2018,
            "expected_screen_size": "1080p",
            "expected_group": "KG",
            "is_episode": False,
        },
        {
            "filename": "District.9.2009.PROPER.1080p.BluRay.x264-SPARKS.mkv",
            "expected_title": "District 9",
            "expected_year": 2009,
            "expected_screen_size": "1080p",
            "expected_group": "SPARKS",
            "is_episode": False,
        },
        {
            "filename": "The.100.S04E01.720p.HDTV.x264-AVS.mkv",
            "expected_title": "The 100",
            "expected_season": 4,
            "expected_episode": 1,
            "expected_screen_size": "720p",
            "expected_group": "AVS",
            "is_episode": True,
        },
        {
            "filename": "Se7en.1995.Remastered.1080p.BluRay.x264-DON.mkv",
            "expected_title": "Se7en",
            "expected_year": 1995,
            "expected_screen_size": "1080p",
            "expected_group": "DON",
            "is_episode": False,
        },
        {
            "filename": "[SubsPlease] Sousou no Frieren - 01 (1080p) [9A4B8F3C].mkv",
            "expected_title": "Sousou no Frieren",
            "expected_episode": 1,
            "expected_screen_size": "1080p",
            "expected_group": "SubsPlease",
            "is_episode": True,
        },
        {
            "filename": "A.Clockwork.Orange.1971.1080p.BluRay.x264-CtrlHD.mkv",
            "expected_year": 1971,
            "expected_screen_size": "1080p",
            "expected_group": "CtrlHD",
            "is_episode": False,
        },
        {
            "filename": (
                "Crying.Fist.aka.Jumeogi.wunda.2005.KOREAN.PROPER.1080p.BluRay.x264-CiNEFiLE.mkv"
            ),
            "expected_year": 2005,
            "expected_screen_size": "1080p",
            "expected_group": "CiNEFiLE",
            "is_episode": False,
        },
    ]

    for c in cases:
        filename = str(c["filename"])
        file_p = tmp_path / filename
        file_p.write_bytes(b"\x00" * 131072)
        meta = parse_video_metadata(file_p, compute_hash=True)

        if "expected_title" in c:
            assert str(c["expected_title"]).lower() in meta.title.lower()
        if "expected_year" in c:
            assert meta.year == c["expected_year"]
        if "expected_season" in c:
            assert meta.season == c["expected_season"]
        if "expected_episode" in c:
            assert meta.episode == c["expected_episode"]
        if "expected_screen_size" in c:
            assert meta.screen_size == c["expected_screen_size"]
        if "expected_group" in c:
            assert meta.release_group == c["expected_group"]
        assert meta.is_episode == c["is_episode"]
        assert meta.moviehash is not None


def test_world_cinema_and_symbolic_titles(tmp_path: Path) -> None:
    cases = [
        {
            "filename": "WALL-E.2008.2160p.UHD.BluRay.x265-DEPTH.mkv",
            "expected_title": "WALL-E",
            "expected_year": 2008,
            "expected_group": "DEPTH",
        },
        {
            "filename": "3.10.to.Yuma.2007.1080p.BluRay.x264-CtrlHD.mkv",
            "expected_title": "3 10 to Yuma",
            "expected_year": 2007,
            "expected_group": "CtrlHD",
        },
        {
            "filename": "REC.2007.SPANISH.1080p.BluRay.x264-CiNEFiLE.mkv",
            "expected_title": "REC",
            "expected_year": 2007,
            "expected_group": "CiNEFiLE",
        },
        {
            "filename": "9.2009.1080p.BluRay.x264-SPARKS.mkv",
            "expected_title": "9",
            "expected_year": 2009,
            "expected_group": "SPARKS",
        },
        {
            "filename": "Koyaanisqatsi.1982.CRITERION.1080p.BluRay.x264-EA.mkv",
            "expected_title": "Koyaanisqatsi",
            "expected_year": 1982,
            "expected_group": "EA",
        },
        {
            "filename": "Sedmikrasky.1966.CZECH.1080p.BluRay.x264-EA.mkv",
            "expected_title": "Sedmikrasky",
            "expected_year": 1966,
            "expected_group": "EA",
        },
        {
            "filename": "The.Color.of.Pomegranates.1969.ARMENIAN.1080p.BluRay.x264-DEPTH.mkv",
            "expected_title": "The Color of Pomegranates",
            "expected_year": 1969,
            "expected_group": "DEPTH",
        },
        {
            "filename": "Satantango.1994.Part1.HUNGARIAN.1080p.BluRay.x264-GHOULS.mkv",
            "expected_title": "Satantango",
            "expected_year": 1994,
            "expected_group": "GHOULS",
        },
        {
            "filename": "Aferim.2015.RO.1080p.BluRay.x264-ROSub.mkv",
            "expected_title": "Aferim",
            "expected_year": 2015,
            "expected_group": "ROSub",
        },
        {
            "filename": "Bad.Luck.Banging.or.Loony.Porn.2021.RO.1080p.BluRay.x264-ROSub.mkv",
            "expected_title": "Bad Luck Banging or Loony Porn",
            "expected_year": 2021,
            "expected_group": "ROSub",
        },
        {
            "filename": "L.Avventura.1960.CRITERION.1080p.BluRay.x264-EA.mkv",
            "expected_title": "L Avventura",
            "expected_year": 1960,
            "expected_group": "EA",
        },
        {
            "filename": "Touki.Bouki.1973.WOLOF.1080p.BluRay.x264-DEPTH.mkv",
            "expected_title": "Touki Bouki",
            "expected_year": 1973,
            "expected_group": "DEPTH",
        },
        {
            "filename": "Kill.Bill.Vol.1.2003.1080p.BluRay.x264-SPARKS.mkv",
            "expected_title": "Kill Bill",
            "expected_year": 2003,
            "expected_group": "SPARKS",
        },
        {
            "filename": "Gangs.of.Wasseypur.Part.1.2012.HINDI.1080p.BluRay.x264-D-ZON3.mkv",
            "expected_title": "Gangs of Wasseypur",
            "expected_year": 2012,
            "expected_group": "D-ZON3",
        },
        {
            "filename": (
                "Ghost.in.the.Shell.2.Innocence.2004.JAPANESE.1080p.BluRay.x264-Coalgirls.mkv"
            ),
            "expected_title": "Ghost in the Shell 2 Innocence",
            "expected_year": 2004,
            "expected_group": "Coalgirls",
        },
        {
            "filename": "O.Brother.Where.Art.Thou.2000.1080p.BluRay.x264-SPARKS.mkv",
            "expected_title": "O Brother Where Art Thou",
            "expected_year": 2000,
            "expected_group": "SPARKS",
        },
        {
            "filename": "Face.Off.1997.1080p.BluRay.x264-SPARKS.mkv",
            "expected_title": "Face Off",
            "expected_year": 1997,
            "expected_group": "SPARKS",
        },
        {
            "filename": "Pi.1998.REMASTERED.1080p.BluRay.x264-CiNEFiLE.mkv",
            "expected_title": "Pi",
            "expected_year": 1998,
            "expected_group": "CiNEFiLE",
        },
    ]

    for c in cases:
        filename = str(c["filename"])
        file_p = tmp_path / filename
        file_p.write_bytes(b"\x00" * 131072)
        meta = parse_video_metadata(file_p, compute_hash=True)

        assert str(c["expected_title"]).lower() in meta.title.lower()
        assert meta.year == c["expected_year"]
        assert meta.release_group == c["expected_group"]
        assert meta.moviehash is not None


def test_tough_scoring_and_ranking_tiebreakers() -> None:
    meta = VideoMetadata(
        file_path=Path("/tmp/Moartea.Domnului.Lazarescu.2005.1080p.BluRay.x264-ROSub.mkv"),
        title="Moartea Domnului Lazarescu",
        year=2005,
        release_group="ROSub",
        screen_size="1080p",
        source="BluRay",
        video_codec="x264",
        is_episode=False,
        moviehash="fedcba9876543210",
        file_size=1048576,
    )

    candidates = [
        ("Moartea.Domnului.Lazarescu.2005.1080p.BluRay.x264-ROSub", False, 100),
        ("The.Death.of.Mr.Lazarescu.2005.1080p.BluRay.x264-ROSub.REPACK", False, 50),
        ("Moartea.Domnului.Lazarescu.2005.1080p.BluRay.x264-CiNEFiLE", False, 200),
        ("The.Death.of.Mr.Lazarescu.2005.720p.HDTV.x264-DIMENSION", False, 10),
        ("Moartea.Domnului.Lazarescu.DVDRip.XviD-UNKNOWN", False, 0),
        ("Random.Release.Name", True, 0),
    ]

    scored = []
    for rel_name, matched_by_hash, downloads in candidates:
        s = score_subtitle_candidate(
            video_meta=meta,
            release_name=rel_name,
            matched_by_hash=matched_by_hash,
            downloads=downloads,
        )
        scored.append((rel_name, matched_by_hash, s))

    scored.sort(key=lambda x: x[2], reverse=True)

    assert scored[0][1] is True
    assert scored[0][2] >= 100.0

    assert scored[1][0] == "Moartea.Domnului.Lazarescu.2005.1080p.BluRay.x264-ROSub"
    assert scored[-1][0] == "Moartea.Domnului.Lazarescu.DVDRip.XviD-UNKNOWN"


def test_tough_malformed_subtitles() -> None:
    dirty_srt = (
        b"42\r\n"
        b"00:00:01,500 --> 00:00:04,200\r\n"
        b"<i>Salut, <b>lume</b>!</i>\r\n"
        b"\r\n\r\n"
        b"109\r\n"
        b"00:00:05,000 --> 00:00:08,750\r\n"
        b'<font color="#ffff00">- Cum merge treaba?</font>\r\n'
        b"- Foarte bine.\r\n"
    )
    res1 = decode_and_normalize_subtitle_content(dirty_srt)
    decoded1 = res1.decode("utf-8")
    assert "1\n00:00:01,500 --> 00:00:04,200" in decoded1
    assert "2\n00:00:05,000 --> 00:00:08,750" in decoded1
    assert "<i>Salut, <b>lume</b>!</i>" in decoded1

    text_pl = (
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "Zażółć gęślą jaźń - polskie znaki diakrytyczne.\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\n"
        "Druga linia dialogu w filmie fabularnym.\n"
    )
    raw_pl = text_pl.encode("cp1250")
    res_pl = decode_and_normalize_subtitle_content(raw_pl)
    assert "Zażółć" in res_pl.decode("utf-8")
    assert "polskie znaki" in res_pl.decode("utf-8")

    text_ro = (
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "Subtitrare oficială pentru filmul Moartea Domnului Lăzărescu.\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\n"
        "Vă rugăm să aveţi răbdare cu pacientul.\n"
    )
    raw_ro = text_ro.encode("cp1250")
    res_ro = decode_and_normalize_subtitle_content(raw_ro)
    assert "Lăzărescu" in res_ro.decode("utf-8") or "Lazarescu" in res_ro.decode("utf-8")


def test_multilingual_and_special_character_subtitles() -> None:
    # 1. Greek
    text_el = (
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "Γεια σου κόσμε, ελληνικοί υπότιτλοι για ταινία.\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\n"
        "Δεύτερη γραμμή διαλόγου.\n"
    )
    res_el = decode_and_normalize_subtitle_content(text_el.encode("cp1253"))
    assert "ελληνικοί υπότιτλοι" in res_el.decode("utf-8")

    # 2. Czech
    text_cs = (
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "Příliš žluťoučký kůň úpěl ďábelské ódy pro film.\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\n"
        "Druhá řádka textu v českém jazyce.\n"
    )
    res_cs = decode_and_normalize_subtitle_content(text_cs.encode("cp1250"))
    assert "žluťoučký kůň" in res_cs.decode("utf-8")

    # 3. Japanese (CJK UTF-8)
    text_ja = (
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "こんにちは世界、映画の日本語字幕です。\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\n"
        "第二行目のセリフです。\n"
    )
    res_ja = decode_and_normalize_subtitle_content(text_ja.encode("utf-8"))
    assert "映画の日本語字幕" in res_ja.decode("utf-8")

    # 4. Arabic (UTF-8)
    text_ar = (
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "مرحبا بالعالم، ترجمة الفيلم السينمائي.\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\n"
        "سطر الحوار الثاني في الترجمة.\n"
    )
    res_ar = decode_and_normalize_subtitle_content(text_ar.encode("utf-8"))
    assert "ترجمة الفيلم" in res_ar.decode("utf-8")

    # 5. European accents and music/quote symbols (UTF-8 BOM)
    text_euro = (
        "1\n00:00:01,000 --> 00:00:04,000\n"
        "♫ Le cœur d’Amélie — Schön & ¡Olé! «100%»… ♫\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\n"
        "Deuxième ligne avec des caractères spéciaux.\n"
    )
    res_euro = decode_and_normalize_subtitle_content(text_euro.encode("utf-8-sig"))
    decoded_euro = res_euro.decode("utf-8")
    assert "♫ Le cœur d’Amélie" in decoded_euro
    assert "¡Olé!" in decoded_euro
    assert "«100%»…" in decoded_euro
