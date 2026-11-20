from pathlib import Path


def test_game_source_uses_authoritative_science_endpoints():
    source = Path("game/src/api.ts").read_text(encoding="utf-8")
    assert "/api/v1/runs/" in source
    assert "/api/v1/environment/" in source
    assert "/api/v1/planet-scenarios" in source
    assert "Math.random" not in source


def test_game_source_preserves_si_and_unknown_semantics():
    source = Path("game/src/main.ts").read_text(encoding="utf-8")
    assert "gravity_m_s2" in source
    assert "stellarAngularRadius" in source
    assert "UNAVAILABLE" in source
    assert "sourceHash" in Path("game/src/save.ts").read_text(encoding="utf-8")
