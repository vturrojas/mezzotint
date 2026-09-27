"""Print styles.

Each style is a pairing of an *art direction* (what the image model is told to
make) and a *plate* (how the ink engine separates and screens it for a
three-ink e-paper panel). The two halves are tuned together: a linocut wants a
hard threshold, an etching wants fine error diffusion, a risograph wants a
halftone screen. That pairing is the whole trick.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Plate:
    screen: str = "atkinson"  # see ink.SCREENS
    red: float = 0.6  # 0 = never use red ink, 1 = reach for it eagerly
    contrast: float = 1.15  # tonal contrast before screening
    gamma: float = 1.0  # >1 opens shadows, <1 deepens them
    sharpen: float = 0.6  # unsharp amount; tiny panels need a little bite


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    blurb: str
    directive: str  # appended verbatim to every image prompt
    plate: Plate = field(default_factory=Plate)

    def public(self) -> dict:
        d = asdict(self)
        d.pop("directive")
        return d


# The common constraint every image prompt carries. The panel is roughly
# 400x300 pixels (Inky wHAT) with exactly three inks, so the model must be pushed toward
# bold silhouettes, flat fields and generous negative space.
INK_CONSTRAINT = (
    "two-color print using only black ink and one vermilion red ink on warm "
    "off-white paper, flat unmodulated color fields, bold readable silhouette, "
    "one clear focal subject, generous negative space, 4:3 landscape "
    "composition, no text, no lettering, no signature, no border"
)

STYLES: list[Style] = [
    Style(
        id="riso",
        name="Risograph",
        blurb="Two-drum riso pull. Grain, overprint, slight misregistration.",
        directive=(
            "risograph print, visible halftone grain, slight misregistration "
            "between the black and red layers, overprinted shapes, "
            "zine aesthetic"
        ),
        plate=Plate(screen="halftone", red=0.75, contrast=1.2, sharpen=0.4),
    ),
    Style(
        id="linocut",
        name="Linocut",
        blurb="Carved relief block. Gouge marks, hard edges, no greys.",
        directive=(
            "linocut relief print, carved gouge marks, hard edges, heavy black "
            "areas, red used as a single accent block, hand-pulled texture"
        ),
        plate=Plate(screen="threshold", red=0.55, contrast=1.45, sharpen=0.2),
    ),
    Style(
        id="swiss",
        name="Swiss Poster",
        blurb="International Typographic Style. Grid, geometry, restraint.",
        directive=(
            "International Typographic Style poster, strict grid, geometric "
            "abstraction, circles and bars, asymmetric balance, modernist, "
            "large flat red shapes against black and paper"
        ),
        plate=Plate(screen="threshold", red=0.9, contrast=1.3, sharpen=0.0),
    ),
    Style(
        id="woodblock",
        name="Woodblock",
        blurb="Ukiyo-e sensibility. Outlines, wave patterns, a red sun.",
        directive=(
            "Japanese woodblock print, confident black keyblock outlines, "
            "stylised waves and clouds, flat red sun or accent, wood grain "
            "texture"
        ),
        plate=Plate(screen="atkinson", red=0.6, contrast=1.25, sharpen=0.5),
    ),
    Style(
        id="etching",
        name="Etching",
        blurb="Intaglio line work. Crosshatch, drypoint, a red chop mark.",
        directive=(
            "copperplate etching, fine crosshatching and stippling, intaglio "
            "line work, dramatic chiaroscuro, a small red seal stamp accent"
        ),
        plate=Plate(screen="jarvis", red=0.3, contrast=1.1, gamma=1.1, sharpen=0.9),
    ),
    Style(
        id="constructivist",
        name="Constructivist",
        blurb="Avant-garde diagonals. Photomontage energy, heroic scale.",
        directive=(
            "constructivist avant-garde poster, dynamic diagonals, heroic low "
            "angle, bold red wedges and bars, high-contrast black photomontage "
            "silhouettes"
        ),
        plate=Plate(screen="floyd", red=0.85, contrast=1.35, sharpen=0.5),
    ),
    Style(
        id="sumi",
        name="Sumi-e",
        blurb="Brush and ink wash. Mostly paper, a vermilion seal.",
        directive=(
            "sumi-e ink wash painting, expressive single brush strokes, "
            "mostly empty paper, soft ink gradients, a small vermilion hanko "
            "seal"
        ),
        plate=Plate(screen="atkinson", red=0.35, contrast=1.05, gamma=1.15, sharpen=0.4),
    ),
    Style(
        id="stencil",
        name="Stencil",
        blurb="Cut-paper street stencil. Two layers, spray edges.",
        directive=(
            "two-layer spray paint stencil, cut-paper shapes, slight overspray "
            "at the edges, bold graphic silhouette, urban poster"
        ),
        plate=Plate(screen="bayer", red=0.7, contrast=1.4, sharpen=0.3),
    ),
]

STYLE_BY_ID = {s.id: s for s in STYLES}
DEFAULT_STYLE = "riso"


def get_style(style_id: str | None) -> Style:
    return STYLE_BY_ID.get(style_id or DEFAULT_STYLE, STYLE_BY_ID[DEFAULT_STYLE])
