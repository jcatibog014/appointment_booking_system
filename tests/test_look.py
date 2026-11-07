"""The look: fonts exist, both dark modes agree, the accent is purple, and the logo stays solid."""
import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "shiromoto" / "static"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def block(selector_start):
    start = CSS.index(selector_start)
    return CSS[start:CSS.index("\n}", start)]


def tokens(text):
    return dict(re.findall(r"(--[a-z-]+):\s*([^;]+);", text))


def test_every_file_the_stylesheet_loads_exists():
    paths = re.findall(r'url\("([^"]+)"\)', CSS)
    assert paths, "the fonts should be loaded with url(...)"
    assert [path for path in paths if not (STATIC / path).exists()] == []


def test_the_pages_preload_both_fonts(client):
    page = client.get("/login").get_data(as_text=True)
    preloaded = re.findall(r'<link rel="preload" href="/static/(fonts/[^"]+)"', page)
    assert preloaded == ["fonts/saira-condensed-700.woff2", "fonts/inter-variable.woff2"]
    assert all(f'url("{path}")' in CSS for path in preloaded)


def test_saira_condensed_for_headings_and_inter_for_the_rest():
    theme = tokens(block(":root {"))
    assert theme["--headline"].startswith('"Saira Condensed"') and theme["--ui"].startswith('"Inter"')
    assert "Barlow" not in CSS


def test_the_phone_app_keeps_every_font():
    """The service worker keeps a copy of each font for offline use."""
    kept = (STATIC / "sw.js").read_text(encoding="utf-8")
    fonts = [path for path in re.findall(r'url\("([^"]+)"\)', CSS) if path.startswith("fonts/")]
    assert fonts and all(f'"/static/{path}"' in kept for path in fonts)


def test_both_dark_modes_say_the_same():
    """Dark mode is written twice (device setting, and the choice in Settings). They must match."""
    device = tokens(block("@media (prefers-color-scheme: dark)"))
    chosen = tokens(block(':root[data-theme="dark"]'))
    assert device == chosen


def test_every_dark_token_exists_in_light_mode():
    light = tokens(block(":root {"))
    dark = tokens(block(':root[data-theme="dark"]'))
    assert set(dark) <= set(light)


def test_the_accent_is_purple_not_orange():
    # The owner chose Garage night with a purple accent.
    assert tokens(block(":root {"))["--accent"] == "#7c3aed"
    assert tokens(block(':root[data-theme="dark"]'))["--accent"] == "#a78bfa"


def luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one, two):
    a, b = sorted((luminance(one), luminance(two)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def test_text_and_buttons_are_easy_to_read():
    for theme in (tokens(block(":root {")), tokens(block(':root[data-theme="dark"]'))):
        assert contrast(theme["--ink"], theme["--surface"]) >= 7
        assert contrast(theme["--ink-soft"], theme["--surface"]) >= 4.5
        assert contrast(theme["--accent"], theme["--surface"]) >= 4.5  # links
        assert contrast(theme["--on-accent"], theme["--accent"]) >= 4.5  # button text
        assert contrast(theme["--control-border"], theme["--surface"]) >= 3  # outlined buttons
        assert contrast(theme["--info-ink"], theme["--info-soft"]) >= 4.5  # calm pills: waiting for someone
        assert contrast(theme["--danger"], theme["--danger-soft"]) >= 4.5  # urgent pills: the shop must act
    light = tokens(block(":root {"))
    assert contrast(light["--side-ink"], light["--side-bg"]) >= 7
    assert contrast(light["--side-accent"], light["--side-bg"]) >= 4.5


def test_the_logo_tile_is_solid_everywhere():
    for selector in (".wordmark .mark {", ".welcome-panel .wordmark .mark {"):
        start = CSS.index(selector)
        rule = CSS[start:CSS.index("}", start)]
        assert "--mark-nut: var(" in rule and "rgb(255 255 255 /" not in rule
