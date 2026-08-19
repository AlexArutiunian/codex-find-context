from codex_context.app import CSS


def test_responsive_breakpoints_and_mobile_controls_are_present():
    assert "@media (max-width: 1100px)" in CSS
    assert "@media (max-width: 720px)" in CSS
    assert "@media (max-width: 430px)" in CSS
    assert ".history-controls" in CSS
    assert "grid-template-columns" in CSS
    assert "#search-results" in CSS
    assert "font-size: 16px" in CSS
