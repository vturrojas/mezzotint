import numpy as np
import pytest
from PIL import Image

from mezzotint import ink
from mezzotint.proof import sampler
from mezzotint.styles import STYLES


@pytest.fixture(scope="module")
def scene():
    return sampler((800, 600))


@pytest.mark.parametrize("size", [(400, 300), (250, 122), (212, 104)])
@pytest.mark.parametrize("screen", list(ink.SCREENS))
def test_every_screen_yields_a_valid_plate(scene, size, screen):
    plate = ink.render(scene, size, ink.PlateSettings(screen=screen))
    assert plate.shape == (size[1], size[0])
    assert plate.dtype == np.uint8
    assert set(np.unique(plate)) <= {ink.PAPER, ink.BLACK, ink.RED}


def test_red_goes_to_red_and_blue_does_not():
    red = Image.new("RGB", (100, 75), (215, 45, 35))
    blue = Image.new("RGB", (100, 75), (40, 70, 200))
    s = ink.PlateSettings(screen="threshold", red=0.7, sharpen=0)
    assert ink.ink_coverage(ink.render(red, (40, 30), s))["red"] > 0.9
    assert ink.ink_coverage(ink.render(blue, (40, 30), s))["red"] == 0.0


def test_red_zero_disables_red_plate(scene):
    plate = ink.render(scene, (200, 150), ink.PlateSettings(screen="floyd", red=0.0))
    assert ink.ink_coverage(plate)["red"] == 0.0


def test_matted_mount_has_paper_margin_and_colophon(scene):
    plate = ink.render(scene, (400, 300), ink.PlateSettings(matte="matted"), title="Test Title", edition=7)
    assert (plate[:, 0] == ink.PAPER).all()  # outer margin stays paper
    colophon = plate[-12:, :]
    assert (colophon == ink.BLACK).any() and (colophon == ink.RED).any()  # title + red edition number


def test_settings_clamp_rejects_nonsense():
    s = ink.PlateSettings(screen="nope", red=5, contrast=-1, matte="gold").clamp()
    assert s.screen == "atkinson" and s.red == 1.0 and s.contrast == 0.5 and s.matte == "bleed"


def test_fit_crops_to_aspect_around_focus():
    im = Image.new("RGB", (1000, 100))
    im.paste((255, 0, 0), (900, 0, 1000, 100))  # red strip on the far right
    right = ink.fit(im, (40, 30), fx=1.0)
    left = ink.fit(im, (40, 30), fx=0.0)
    assert np.asarray(right)[..., 0].mean() > np.asarray(left)[..., 0].mean()


def test_text_and_truncation():
    assert ink.text_width("MM") > ink.text_width("II")
    t = ink._truncate("A very long title that cannot possibly fit", 60)
    assert ink.text_width(t) <= 60 and t.endswith(".")


def test_welcome_card_contains_a_qr_and_red_accent():
    plate = ink.welcome_card((400, 300), "http://mezzotint.local/")
    cov = ink.ink_coverage(plate)
    assert cov["black"] > 0.1 and cov["red"] > 0.0


def test_palette_image_indices_match_inky():
    plate = np.array([[0, 1, 2]], dtype=np.uint8)
    im = ink.to_palette_image(plate)
    assert im.mode == "P" and list(np.asarray(im)[0]) == [0, 1, 2]


def test_every_style_has_a_known_screen():
    for s in STYLES:
        assert s.plate.screen in ink.SCREENS
