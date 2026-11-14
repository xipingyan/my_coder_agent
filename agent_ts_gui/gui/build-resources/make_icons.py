#!/usr/bin/env python3
"""Regenerate the app icon PNGs/ICO from icon.svg.

Usage:  python make_icons.py
Requires: cairosvg + Pillow (installed in backend/.venv).
Outputs (in this directory): icon-512.png, icon-128.png, icon.ico
"""
import os
import sys

try:
    import cairosvg
    from PIL import Image
except ImportError:
    sys.exit("need cairosvg + Pillow:  backend/.venv/bin/pip install cairosvg pillow")

here = os.path.dirname(os.path.abspath(__file__))
svg = os.path.join(here, "icon.svg")

cairosvg.svg2png(url=svg, write_to=os.path.join(here, "icon-512.png"),
                 output_width=512, output_height=512)
cairosvg.svg2png(url=svg, write_to=os.path.join(here, "icon-128.png"),
                 output_width=128, output_height=128)
Image.open(os.path.join(here, "icon-512.png")).save(
    os.path.join(here, "icon.ico"),
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icons regenerated in", here)
