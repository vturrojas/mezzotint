"""The art director: turns a loose theme into one specific, printable image.

A theme like "the ocean" is a mood, not a picture. Every pull of the press
asks a local LLM to commit to a single concrete scene inside that theme,
composed for a 400x300 three-ink panel, and to title it like a print. Recent
titles are passed back in so the frame keeps surprising you instead of
drawing the same lighthouse every hour.
"""

from __future__ import annotations

import logging
import random
import re

from ..styles import INK_CONSTRAINT, Style

log = logging.getLogger("mezzotint.studio.art")

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "scene": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["title", "scene", "note"],
}

SYSTEM = """You are the art director of a tiny print studio. Each hour you commission one
new print for a small e-paper frame (400x300 pixels) that can show ONLY three
inks: black, vermilion red, and bare off-white paper. You never see colour
beyond those three.

Given a THEME and a PRINT STYLE, invent ONE specific scene that belongs to the
theme. Be concrete and surprising: choose a particular subject, a vantage
point, a time of day, and what the red ink is reserved for. Think like a
poster designer:
- one dominant subject with a strong silhouette that reads at thumbnail size
- big simple shapes and generous empty paper; nothing fussy or tiny
- red is precious: give it to one or two things that deserve emphasis
- landscape 4:3 framing
- never ask for words, letters, logos, signatures or borders in the image
- nothing sexual, gory or cruel; people only as silhouettes or at a distance

Return JSON with:
  title: an evocative print title, 2 to 5 words, no quotes
  scene: one image-generation prompt sentence (25-45 words) describing the
         subject, composition and where the red goes; do not restate the style
  note:  one short sentence, the art director's reason for this choice"""


def _clean(s: str, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip("\"'“”")
    return s[:limit]


def build_image_prompt(scene: str, style: Style) -> str:
    """The style directive and ink constraint are always appended here rather
    than trusted to the LLM, so the look holds even when the LLM drifts."""
    return f"{scene.rstrip('. ')}. {style.directive}. {INK_CONSTRAINT}."


_FALLBACK_FRAMES = [
    ("{t}, seen from far away at dusk", "Distance keeps the shapes large."),
    ("a single {t} emblem, centred, monumental", "One icon, nothing else."),
    ("{t} at dawn with a low red sun", "Red sun, black silhouette: the classic pull."),
    ("close crop of {t}, dramatic diagonal", "Cropped tight for tension."),
    ("{t} reflected in still water", "Symmetry holds up at low resolution."),
]


def fallback(theme: str, style: Style, rng: random.Random | None = None) -> dict:
    """Deterministic-enough composition when no LLM is available."""
    rng = rng or random.Random()
    frame, note = rng.choice(_FALLBACK_FRAMES)
    t = theme.strip() or "a quiet harbour"
    scene = frame.format(t=t)
    words = [w for w in re.findall(r"[A-Za-z']+", t)][:3]
    title = " ".join(w.capitalize() for w in words) or "Untitled"
    return {"title": title, "scene": scene, "note": note, "director": "fallback"}


async def direct(ollama, llm: str | None, theme: str, style: Style, recent_titles: list[str]) -> dict:
    """Ask the LLM for a composition; fall back to templates on any failure."""
    if not llm:
        return fallback(theme, style)
    avoid = ", ".join(recent_titles[-12:]) or "none yet"
    user = (
        f"THEME: {theme or 'surprise me: anything beautiful'}\n"
        f"PRINT STYLE: {style.name} — {style.blurb}\n"
        f"RECENT PRINTS (do something clearly different): {avoid}\n"
        f"Commission print number {random.randint(1, 10_000)}."
    )
    try:
        out = await ollama.chat_json(llm, SYSTEM, user, SCHEMA, temperature=1.0)
        title = _clean(out.get("title"), 48) or "Untitled"
        scene = _clean(out.get("scene"), 400)
        note = _clean(out.get("note"), 160)
        if len(scene) < 12:
            raise ValueError("scene too short")
        return {"title": title, "scene": scene, "note": note, "director": llm}
    except Exception as e:  # noqa: BLE001 - any failure degrades gracefully
        log.warning("art director failed (%s); using fallback", e)
        return fallback(theme, style)
