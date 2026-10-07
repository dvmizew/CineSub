from __future__ import annotations

from pathlib import Path

from cinesub.core.models import VideoMetadata
from cinesub.core.utils import (
    decode_and_normalize_subtitle_content,
    evaluate_local_subtitle_score,
    get_subtitle_max_timestamp,
    parse_video_metadata,
    score_subtitle_candidate,
    validate_subtitle_timing,
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


def test_popular_and_cult_tv_series(tmp_path: Path) -> None:
    cases = [
        {
            "filename": "Game.of.Thrones.S08E06.1080p.WEB-DL.DDP5.1.Atmos.H.264-CMRG.mkv",
            "expected_title": "Game of Thrones",
            "expected_season": 8,
            "expected_episode": 6,
            "expected_group": "CMRG",
            "is_episode": True,
        },
        {
            "filename": (
                "House.of.the.Dragon.S02E01.2160p.MAX.WEB-DL.DDP5.1.Atmos.DoVi.H.265-FLUX.mkv"
            ),
            "expected_title": "House of the Dragon",
            "expected_season": 2,
            "expected_episode": 1,
            "expected_group": "FLUX",
            "is_episode": True,
        },
        {
            "filename": "The.Sopranos.S01E01.1080p.BluRay.x264-ROVERS.mkv",
            "expected_title": "The Sopranos",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "ROVERS",
            "is_episode": True,
        },
        {
            "filename": "The.Wire.S04E01.1080p.BluRay.x264-CtrlHD.mkv",
            "expected_title": "The Wire",
            "expected_season": 4,
            "expected_episode": 1,
            "expected_group": "CtrlHD",
            "is_episode": True,
        },
        {
            "filename": "Stranger.Things.S04E09.1080p.NF.WEB-DL.DDP5.1.Atmos.x264-FLUX.mkv",
            "expected_title": "Stranger Things",
            "expected_season": 4,
            "expected_episode": 9,
            "expected_group": "FLUX",
            "is_episode": True,
        },
        {
            "filename": "Severance.S01E01.1080p.ATVP.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv",
            "expected_title": "Severance",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "FLUX",
            "is_episode": True,
        },
        {
            "filename": "Succession.S04E03.1080p.MAX.WEB-DL.DDP5.1.H.264-FLUX.mkv",
            "expected_title": "Succession",
            "expected_season": 4,
            "expected_episode": 3,
            "expected_group": "FLUX",
            "is_episode": True,
        },
        {
            "filename": "Chernobyl.S01E01.1080p.BluRay.x264-ROVERS.mkv",
            "expected_title": "Chernobyl",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "ROVERS",
            "is_episode": True,
        },
        {
            "filename": "Better.Call.Saul.S06E07.1080p.WEB.H264-CAKES.mkv",
            "expected_title": "Better Call Saul",
            "expected_season": 6,
            "expected_episode": 7,
            "expected_group": "CAKES",
            "is_episode": True,
        },
        {
            "filename": "Twin.Peaks.S03E08.1080p.AMZN.WEBRip.DDP5.1.x264-NTb.mkv",
            "expected_title": "Twin Peaks",
            "expected_season": 3,
            "expected_episode": 8,
            "expected_group": "NTb",
            "is_episode": True,
        },
        {
            "filename": "Riget.S01E01.DANISH.1080p.BluRay.x264-EA.mkv",
            "expected_title": "Riget",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "EA",
            "is_episode": True,
        },
        {
            "filename": "Dekalog.S01E01.POLISH.1080p.BluRay.x264-EA.mkv",
            "expected_title": "Dekalog",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "EA",
            "is_episode": True,
        },
        {
            "filename": "Berlin.Alexanderplatz.E01.GERMAN.1080p.BluRay.x264-EA.mkv",
            "expected_title": "Berlin Alexanderplatz",
            "expected_episode": 1,
            "expected_group": "EA",
            "is_episode": True,
        },
        {
            "filename": "Umbre.S02E01.ROMANIAN.1080p.HBO.WEB-DL.AAC2.0.H.264-ROSub.mkv",
            "expected_title": "Umbre",
            "expected_season": 2,
            "expected_episode": 1,
            "expected_group": "ROSub",
            "is_episode": True,
        },
        {
            "filename": "Dark.S01E01.GERMAN.1080p.NF.WEB-DL.DDP5.1.x264-NTb.mkv",
            "expected_title": "Dark",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "NTb",
            "is_episode": True,
        },
        {
            "filename": "Babylon.Berlin.S01E01.GERMAN.1080p.BluRay.x264-CiNEFiLE.mkv",
            "expected_title": "Babylon Berlin",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "CiNEFiLE",
            "is_episode": True,
        },
        {
            "filename": "Gomorra.S01E01.ITALIAN.1080p.BluRay.x264-CiNEFiLE.mkv",
            "expected_title": "Gomorra",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "CiNEFiLE",
            "is_episode": True,
        },
        {
            "filename": "Bron.Broen.S01E01.SWEDISH.1080p.BluRay.x264-CiNEFiLE.mkv",
            "expected_title": "Bron Broen",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "CiNEFiLE",
            "is_episode": True,
        },
        {
            "filename": "Neon.Genesis.Evangelion.S01E01.1080p.BluRay.x264-Sephiroth.mkv",
            "expected_title": "Neon Genesis Evangelion",
            "expected_season": 1,
            "expected_episode": 1,
            "expected_group": "Sephiroth",
            "is_episode": True,
        },
        {
            "filename": "Attack.on.Titan.S03E01.1080p.BluRay.x264-DameDesuYo.mkv",
            "expected_title": "Attack on Titan",
            "expected_season": 3,
            "expected_episode": 1,
            "expected_group": "DameDesuYo",
            "is_episode": True,
        },
        {
            "filename": "Monster.E01.Herr.Dr.Tenma.1080p.BluRay.x264-Coalgirls.mkv",
            "expected_title": "Monster",
            "expected_episode": 1,
            "expected_group": "Coalgirls",
            "is_episode": True,
        },
    ]

    for c in cases:
        filename = str(c["filename"])
        file_p = tmp_path / filename
        file_p.write_bytes(b"\x00" * 131072)
        meta = parse_video_metadata(file_p, compute_hash=True)

        assert str(c["expected_title"]).lower() in meta.title.lower()
        if "expected_season" in c:
            assert meta.season == c["expected_season"]
        if "expected_episode" in c:
            assert meta.episode == c["expected_episode"]
        if "expected_group" in c:
            assert meta.release_group == c["expected_group"]
        assert meta.is_episode == c["is_episode"]
        assert meta.moviehash is not None


def test_yts_and_1337x_release_group_matching(tmp_path: Path) -> None:
    yts_file = tmp_path / "Dune.Part.Two.2024.1080p.WEBRip.x264-[YTS.MX].mp4"
    yts_file.write_bytes(b"\x00" * 131072)
    yts_meta = parse_video_metadata(yts_file, compute_hash=True)

    assert "dune part 2" in yts_meta.title.lower()
    assert yts_meta.year == 2024
    assert yts_meta.release_group == "YTS.MX"

    score_yify = score_subtitle_candidate(
        yts_meta, "Dune.Part.Two.2024.1080p.WEBRip.x264-YIFY", matched_by_hash=False
    )
    score_yts = score_subtitle_candidate(
        yts_meta, "Dune.Part.Two.2024.1080p.WEBRip.x264-[YTS.AM]", matched_by_hash=False
    )
    score_random = score_subtitle_candidate(
        yts_meta, "Dune.Part.Two.2024.720p.HDTV.x264-UNKNOWN", matched_by_hash=False
    )

    assert score_yify > score_random
    assert score_yts > score_random
    assert score_yify == score_yts

    qxr_file = tmp_path / "Interstellar.2014.IMAX.1080p.BluRay.x265.10bit-QxR.mkv"
    qxr_file.write_bytes(b"\x00" * 131072)
    qxr_meta = parse_video_metadata(qxr_file, compute_hash=True)

    score_tigole = score_subtitle_candidate(
        qxr_meta, "Interstellar.2014.1080p.BluRay.x265-Tigole", matched_by_hash=False
    )
    assert score_tigole > score_random


def test_multipart_movies_title_enrichment(tmp_path: Path) -> None:
    cases = [
        ("Harry.Potter.And.The.Deathly.Hallows.Part.1.2010.1080p.mp4", "Part 1"),
        ("Gangs.of.Wasseypur.Part.1.2012.HINDI.1080p.mkv", "Part 1"),
        ("Rebel.Moon.Part.Two.2024.1080p.WEB.mkv", "Part 2"),
    ]

    for filename, expected_part in cases:
        p = tmp_path / filename
        p.write_bytes(b"\x00" * 131072)
        meta = parse_video_metadata(p, compute_hash=False)
        assert expected_part.lower() in meta.title.lower()


def test_real_yts_library_parsing(tmp_path: Path) -> None:
    cases = [
        (
            "El.Ser.Querido.2026.REPACK.1080p.WEBRip.x265.10bit.AAC5.1-[YTS.GG - YTS.BZ].mp4",
            "El Ser Querido",
            2026,
        ),
        ("Glass.2019.1080p.WEBRip.x264-[YTS.AM].mp4", "Glass", 2019),
        ("Inception.2010.1080p.BrRip.x264.YIFY.mp4", "Inception", 2010),
        ("Interstellar.2014.2014.1080p.BluRay.x264.YIFY.mp4", "Interstellar", 2014),
        (
            "Khake.Sar.Beh.Mohr.1977.1080p.WEBRip.x264.AAC-[YTS.GG - YTS.BZ].mp4",
            "Khake Sar Beh Mohr",
            1977,
        ),
        (
            "Mors.Elling.2003.NORWEGIAN.1080p.WEBRip.x264.AAC5.1-[YTS.GG - YTS.BZ].mp4",
            "Mors Elling",
            2003,
        ),
        (
            "Peter.Gabriel.Growing.Up.Live.2003.1080p.WEBRip.x264.AAC5.1-[YTS.GG - YTS.BZ].mp4",
            "Peter Gabriel Growing Up Live",
            2003,
        ),
        ("The.Mongoose.2026.1080p.WEBRip.x264.AAC-[YTS.GG - YTS.BZ].mp4", "The Mongoose", 2026),
        (
            "The.Odyssey.2026.1080p.WEBRip.x265.10bit.AAC5.1-[YTS.GG - YTS.BZ].mp4",
            "The Odyssey",
            2026,
        ),
    ]

    for fname, exp_title, exp_year in cases:
        p = tmp_path / fname
        p.write_bytes(b"\x00" * 131072)
        meta = parse_video_metadata(p, compute_hash=True)
        assert meta.title.lower() == exp_title.lower()
        assert meta.year == exp_year
        assert meta.moviehash is not None


def test_comprehensive_scene_and_p2p_group_clusters(tmp_path: Path) -> None:
    # 1. Remux / Top-tier Encode Cluster (FraMeSToR <-> CtrlHD <-> DON <-> iFT <-> w4nk3r)
    f_remux = tmp_path / "Inception.2010.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FraMeSToR.mkv"
    f_remux.write_bytes(b"\x00" * 131072)
    meta_remux = parse_video_metadata(f_remux, compute_hash=False)
    score_ctrlhd = score_subtitle_candidate(meta_remux, "Inception.2010.1080p.BluRay.x264-CtrlHD")
    score_other = score_subtitle_candidate(meta_remux, "Inception.2010.1080p.BluRay.x264-UNKNOWN")
    assert score_ctrlhd > score_other

    # 2. High-tier WEB-DL Cluster (NTb <-> FLUX <-> CMRG <-> KiNGS <-> LAZY <-> TEPES)
    f_web = tmp_path / "The.Bear.S02E01.1080p.HULU.WEB-DL.DDP5.1.Atmos.H.264-FLUX.mkv"
    f_web.write_bytes(b"\x00" * 131072)
    meta_web = parse_video_metadata(f_web, compute_hash=False)
    score_ntb = score_subtitle_candidate(
        meta_web, "The.Bear.S02E01.1080p.WEB-DL.DDP5.1.Atmos.H.264-NTb"
    )
    assert score_ntb > score_other

    # 3. Compact / Micro-encoders (PSA <-> GalaxyRG <-> TGx <-> PaHe)
    f_psa = tmp_path / "Dune.2021.1080p.10bit.WEBRip.6CH.x265.HEVC-PSA.mkv"
    f_psa.write_bytes(b"\x00" * 131072)
    meta_psa = parse_video_metadata(f_psa, compute_hash=False)
    score_tgx = score_subtitle_candidate(meta_psa, "Dune.2021.1080p.WEBRip.x264-GalaxyRG")
    assert score_tgx > score_other

    # 4. Scene Giants (SPARKS <-> ROVERS <-> AMIABLE <-> GECKOS <-> DRONES)
    f_scene = tmp_path / "The.Matrix.1999.1080p.BluRay.x264-SPARKS.mkv"
    f_scene.write_bytes(b"\x00" * 131072)
    meta_scene = parse_video_metadata(f_scene, compute_hash=False)
    score_rovers = score_subtitle_candidate(meta_scene, "The.Matrix.1999.1080p.BluRay.x264-ROVERS")
    assert score_rovers > score_other

    # 5. Anime Release & Subbing Groups (Erai-raws <-> SubsPlease <-> HorribleSubs)
    f_anime = tmp_path / "[Erai-raws] Jujutsu Kaisen - 01 [1080p][HEVC].mkv"
    f_anime.write_bytes(b"\x00" * 131072)
    meta_anime = parse_video_metadata(f_anime, compute_hash=False)
    score_subsplease = score_subtitle_candidate(
        meta_anime, "[SubsPlease] Jujutsu Kaisen - 01 (1080p)"
    )
    assert score_subsplease > score_other

    # 6. Automated & Micro TV Encoders (MeGusta <-> SURCODE <-> PiGNUS <-> EDITH)
    f_megusta = tmp_path / "Succession.S04E01.720p.HDTV.x265-MeGusta.mkv"
    f_megusta.write_bytes(b"\x00" * 131072)
    meta_megusta = parse_video_metadata(f_megusta, compute_hash=False)
    score_surcode = score_subtitle_candidate(
        meta_megusta, "Succession.S04E01.720p.HDTV.x264-SURCODE"
    )
    assert score_surcode > score_other

    # 7. Asian Trackers (MTeam <-> FRDS <-> CHDBits <-> OurBits)
    f_mteam = tmp_path / "Parasite.2019.KOREAN.1080p.BluRay.x265-MTeam.mkv"
    f_mteam.write_bytes(b"\x00" * 131072)
    meta_mteam = parse_video_metadata(f_mteam, compute_hash=False)
    score_frds = score_subtitle_candidate(meta_mteam, "Parasite.2019.KOREAN.1080p.BluRay.x264-FRDS")
    assert score_frds > score_other

    # 8. Eastern European / FileList (PlayHD <-> ROsub <-> FLShare)
    f_playhd = tmp_path / "Umbre.S03E01.1080p.HBO.WEB-DL.x264-PlayHD.mkv"
    f_playhd.write_bytes(b"\x00" * 131072)
    meta_playhd = parse_video_metadata(f_playhd, compute_hash=False)
    score_rosub = score_subtitle_candidate(meta_playhd, "Umbre.S03E01.1080p.HBO.WEB-DL.x264-ROsub")
    assert score_rosub > score_other


def test_short_title_scoring_and_gatekeeper(tmp_path: Path) -> None:
    """Validate strict gatekeeping and penalties for short titles (<= 5 characters)."""
    # 1. Title 'Up' (2009)
    f_up = tmp_path / "Up.2009.1080p.BluRay.x264-SPARKS.mkv"
    f_up.write_bytes(b"\x00" * 131072)
    meta_up = parse_video_metadata(f_up, compute_hash=False)

    # Valid match
    score_valid_up = score_subtitle_candidate(meta_up, "Up.2009.1080p.BluRay.x264-ROVERS")
    assert score_valid_up > 0.0

    # False-positive substring in middle: 'Stand Up Guys'
    score_stand_up = score_subtitle_candidate(meta_up, "Stand.Up.Guys.2012.1080p.BluRay.x264")
    assert score_stand_up == 0.0

    # False-positive word containing 'up': 'Superbad'
    score_superbad = score_subtitle_candidate(meta_up, "Superbad.2007.1080p.BluRay.x264")
    assert score_superbad == 0.0

    # 2. Title '9' (2009)
    f_nine = tmp_path / "9.2009.1080p.BluRay.x264.mkv"
    f_nine.write_bytes(b"\x00" * 131072)
    meta_nine = parse_video_metadata(f_nine, compute_hash=False)

    score_valid_nine = score_subtitle_candidate(meta_nine, "9.2009.1080p.BluRay.x264")
    assert score_valid_nine > 0.0

    # False-positive with preceding title: 'District 9'
    score_district_9 = score_subtitle_candidate(meta_nine, "District.9.2009.1080p.BluRay.x264")
    assert score_district_9 == 0.0

    # 3. Title 'Hero' (1992 vs 2002)
    f_hero_1992 = tmp_path / "Hero.1992.1080p.WEBRip.x264.mp4"
    f_hero_1992.write_bytes(b"\x00" * 131072)
    meta_hero_1992 = parse_video_metadata(f_hero_1992, compute_hash=False)

    score_hero_1992 = score_subtitle_candidate(meta_hero_1992, "Hero.1992.1080p.WEBRip.x264")
    assert score_hero_1992 > 0.0

    score_hero_2002 = score_subtitle_candidate(meta_hero_1992, "Hero.2002.1080p.BluRay.x264")
    assert score_hero_2002 == 0.0


def test_video_duration_and_timing_invariants() -> None:
    """Validate subtitle timestamp extraction and video duration timing bounds."""
    srt_content = (
        "1\n00:00:10,000 --> 00:00:15,000\nHello world!\n\n"
        "2\n01:30:00,000 --> 01:30:10,500\nThe end.\n"
    )
    max_ts = get_subtitle_max_timestamp(srt_content)
    # 1h 30m 10.5s = 5410.5 seconds
    assert abs(max_ts - 5410.5) < 0.1

    # Empty subtitle yields 0.0
    assert get_subtitle_max_timestamp("") == 0.0
    assert get_subtitle_max_timestamp(b"") == 0.0

    # Invariant 1: Valid alignment (video duration 5415s, within 15s)
    assert validate_subtitle_timing(max_ts, 5415.0) is True

    # Invariant 2: Overrun (> 15s past video end of 5390s)
    assert validate_subtitle_timing(max_ts, 5390.0) is False

    # Invariant 3: Drastic early termination (< 70% of video length)
    assert validate_subtitle_timing(max_ts, 9000.0) is False

    # Invariant 4: Unknown video duration fails open (True)
    assert validate_subtitle_timing(max_ts, None) is True
    assert validate_subtitle_timing(max_ts, 0.0) is True

    # Invariant 5: Zero timestamp fails
    assert validate_subtitle_timing(0.0, 5400.0) is False


def test_quality_downgrade_protection(tmp_path: Path) -> None:
    """Validate evaluation of local existing subtitle quality scores."""
    f_video = tmp_path / "Inception.2010.1080p.mkv"
    f_video.write_bytes(b"\x00" * 131072)
    meta = parse_video_metadata(f_video, compute_hash=False)

    # Empty file has 0 score
    f_empty = tmp_path / "Inception.2010.1080p.srt"
    f_empty.write_text("", encoding="utf-8")
    assert evaluate_local_subtitle_score(f_empty, meta) == 0.0

    # Retail / synced subtitle
    f_retail = tmp_path / "Inception.2010.1080p.BluRay.ROSub.srt"
    f_retail.write_text(
        "1\n00:00:01,000 --> 00:00:04,000\nSubtitrare sincronizata de ROSub Retail\n",
        encoding="utf-8",
    )
    score_retail = evaluate_local_subtitle_score(f_retail, meta)
    assert score_retail >= 90.0

    # Machine translated subtitle receives penalty
    f_mt = tmp_path / "Inception.2010.1080p.mt.srt"
    f_mt.write_text(
        "1\n00:00:01,000 --> 00:00:04,000\nMachine Translated by Google\n",
        encoding="utf-8",
    )
    score_mt = evaluate_local_subtitle_score(f_mt, meta)
    assert score_mt < score_retail


def test_real_media_library_fine_tuning(tmp_path: Path) -> None:
    """Fine-tune and verify metadata extraction on real-world filenames.

    Matches media patterns found in local user media libraries.
    """
    real_sample_names = [
        ("Emma..2020.1080p.BluRay.x264.AAC5.1-[YTS.MX].mp4", "Emma", 2020, "YTS.MX"),
        ("Fathers'.Day.1997.1080p.WEBRip.x264.AAC-[YTS.MX].mp4", "Fathers' Day", 1997, "YTS.MX"),
        (
            "James.Vs..His.Future.Self.2019.1080p.WEBRip.x264.AAC5.1-[YTS.MX].mp4",
            "James Vs His Future Self",
            2019,
            "YTS.MX",
        ),
        (
            "Hot.Shots.Part.Deux.1993.1080p.BluRay.x264.AAC-[YTS.MX].mp4",
            "Hot Shots Part Deux",
            1993,
            "YTS.MX",
        ),
        ("Abe.2019.1080p.WEBRip.x264.AAC5.1-[YTS.MX].mp4", "Abe", 2019, "YTS.MX"),
        ("Ill.Be.There.2003.1080p.WEBRip.x264.AAC-[YTS.MX].mp4", "Ill Be There", 2003, "YTS.MX"),
    ]

    for fname, expected_title, expected_year, expected_group in real_sample_names:
        f = tmp_path / fname
        f.write_bytes(b"\x00" * 131072)
        meta = parse_video_metadata(f, compute_hash=False)
        assert meta.title.lower() == expected_title.lower(), (
            f"Mismatch for {fname}: got {meta.title}"
        )
        assert meta.year == expected_year, f"Year mismatch for {fname}: got {meta.year}"
        assert meta.release_group == expected_group, (
            f"Group mismatch for {fname}: got {meta.release_group}"
        )


def test_source_and_codec_alias_clusters() -> None:
    """Validate bidirectional source and video codec alias clusters."""
    video_meta = VideoMetadata(
        file_path=Path("/tmp/21.Bridges.2019.1080p.BluRay.x264.AAC5.1-[YTS.MX].mp4"),
        title="21 Bridges",
        year=2019,
        screen_size="1080p",
        source="Blu-ray",
        video_codec="H.264",
        release_group="YTS.MX",
        is_episode=False,
    )

    # Candidate with alternative alias spelling ('BluRay' vs 'Blu-ray', 'x264' vs 'H.264')
    candidate_name = "21.Bridges.2019.1080p.BluRay.x264.AAC5.1-[YTS.MX]"
    score = score_subtitle_candidate(video_meta, candidate_name)
    assert score >= 90.0

    # Conflicting year rejection for movies
    conflicting_candidate = "21.Bridges.2015.1080p.BluRay.x264.AAC5.1-[YTS.MX]"
    conflicting_score = score_subtitle_candidate(video_meta, conflicting_candidate)
    assert conflicting_score == 0.0
