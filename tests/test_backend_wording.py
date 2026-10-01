"""내레이터가 닿지 않으면 BE 가 제 문구로 대신 말한다. 두 곳의 말이 갈라지지 않는지 BE 소스와 대조한다.

같은 판정을 화면이 날에 따라 다르게 말하면(내레이터가 살아 있느냐에 따라) 신뢰를 깎는다(UX_POLICY 규칙 6).
BE 레포가 옆에 없으면(배포 이미지·CI) 건너뛴다.
"""

import re
from pathlib import Path

import pytest

from app.detections import RULES
from app.switch_timing import HEADLINES, NEVER_RECOUPED

BE = Path(__file__).resolve().parents[2] / "BE_main" / "src" / "main" / "java" / "com" / "palsaekjo" / "yogobi"
pytestmark = pytest.mark.skipif(not BE.exists(), reason="BE_main 레포가 옆에 없다")


def enum_values(path: Path, name: str) -> set[str]:
    body = re.search(rf"enum {name}\s*\{{(.*?)[;}}]", path.read_text(encoding="utf-8"), re.S).group(1)
    return set(re.findall(r"\b[A-Z][A-Z_]+\b", re.sub(r"//.*", "", body)))


def test_detection_wording_matches_backend_fallback():
    assert enum_values(BE / "common" / "DetectionRule.java", "DetectionRule") == set(RULES)
    fallback = (BE / "detection" / "DetectionNarrator.java").read_text(encoding="utf-8")
    for title, how in RULES.values():
        assert f'"{title}"' in fallback and f'"{how}"' in fallback, title


def test_switch_timing_badges_match_backend_fallback():
    assert enum_values(BE / "pricing" / "SwitchTiming.java", "Status") == set(HEADLINES)
    fallback = (BE / "recommend" / "SwitchTimingNarrator.java").read_text(encoding="utf-8")
    for headline in [*HEADLINES.values(), NEVER_RECOUPED]:
        assert f'"{headline}"' in fallback, headline
