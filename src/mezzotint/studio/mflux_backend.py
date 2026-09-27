"""Image generation through mflux (MLX-native diffusion on Apple Silicon).

Ollama disabled image generation in 0.32.6, so the studio draws with mflux
instead: an MIT-licensed MLX port of Z-Image, FLUX.2 and friends. It runs as
one short-lived process per image. That costs a few seconds of model loading
per print, which is irrelevant at an hourly cadence, and it hands every byte
of memory back to the Mac between prints, where other models live too.
"""

from __future__ import annotations

import asyncio
import re
import logging
import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("mezzotint.studio.mflux")

DEFAULT_MODEL = "filipstrand/Z-Image-Turbo-mflux-4bit"
DEFAULT_COMMAND = "mflux-generate-z-image-turbo"


class ImageGenError(RuntimeError):
    pass


class MfluxImager:
    def __init__(self, model: str = DEFAULT_MODEL, steps: int = 9, command: str | None = None,
                 timeout: float = 1800.0):
        self.model = model
        self.steps = steps
        self.timeout = timeout
        # Look next to the running interpreter first: that's the studio's venv.
        search = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
        self.command = command or shutil.which(DEFAULT_COMMAND, path=search)

    def available(self) -> bool:
        return bool(self.command and Path(self.command).exists())

    async def generate(self, prompt: str, size: str, seed: int | None = None) -> bytes:
        if not self.available():
            raise ImageGenError(f"{DEFAULT_COMMAND} not found; run deploy/mac/install.sh to install mflux")
        w, h = (int(v) for v in size.lower().split("x"))
        w, h = w - w % 16, h - h % 16  # diffusion latents want multiples of 16
        seed = random.randint(0, 2**31 - 1) if seed is None else seed
        with tempfile.TemporaryDirectory(prefix="mezzotint-") as tmp:
            out = Path(tmp) / "render.png"
            cmd = [self.command, "--model", self.model, "--prompt", prompt,
                   "--width", str(w), "--height", str(h), "--steps", str(self.steps),
                   "--seed", str(seed), "--output", str(out)]
            log.info("mflux: drawing %sx%s, %s steps, seed %s", w, h, self.steps, seed)
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                text = await asyncio.wait_for(self._pump(proc), timeout=self.timeout)
            except asyncio.TimeoutError:
                proc.kill()
                raise ImageGenError(f"mflux took longer than {int(self.timeout)}s") from None
            if proc.returncode != 0 or not out.exists():
                tail = " | ".join(text.strip().splitlines()[-4:])
                raise ImageGenError(f"mflux failed (exit {proc.returncode}): {tail[:400]}")
            return out.read_bytes()

    @staticmethod
    async def _pump(proc) -> str:
        """Stream mflux's output into the studio log as it arrives (its progress
        bar redraws with carriage returns), so a slow job is visible, not silent."""
        seen: list[str] = []
        buf = ""
        while True:
            chunk = await proc.stdout.read(1024)
            if not chunk:
                break
            buf += chunk.decode(errors="replace")
            *parts, buf = re.split(r"[\r\n]", buf)
            for line in (p.strip() for p in parts):
                if line and (not seen or line != seen[-1]):
                    seen.append(line)
                    if "%|" not in line or line.endswith("]"):
                        log.info("mflux: %s", line[:200])
        if buf.strip():
            seen.append(buf.strip())
        await proc.wait()
        return "\n".join(seen[-50:])
