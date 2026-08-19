from types import SimpleNamespace

from codex_context.app import MESSAGE_WINDOW, _render_messages, _window_max_first, _window_start


def make_messages(count: int):
    return [
        SimpleNamespace(role="user", text=f"message-{index}")
        for index in range(1, count + 1)
    ]


def test_window_defaults_to_latest_400_messages():
    total = 2683

    start = _window_start(total)

    assert start == 2283
    assert _window_max_first(total) == 2284


def test_window_can_jump_to_start_middle_and_end():
    total = 2683

    assert _window_start(total, 1) == 0
    assert _window_start(total, 1000) == 999
    assert _window_start(total, 99999) == 2283


def test_render_messages_limits_html_to_one_window():
    messages = make_messages(MESSAGE_WINDOW + 10)

    html = _render_messages(messages, start=0)

    assert "message-1" in html
    assert f"message-{MESSAGE_WINDOW}" in html
    assert f"message-{MESSAGE_WINDOW + 1}" not in html
    assert "#1" in html
    assert f"#{MESSAGE_WINDOW}" in html
