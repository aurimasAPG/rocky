"""Rocky visual identity — not Hermes gold-on-black.

Terminal apps cannot change the user's typeface. We change palette,
weight, and the wordmark so it does not read as a gold FIGlet clone.
"""

# Slate dusk, apricot coat, coral tongue, sea-glass work.
BG = "1C2B33"
HEADER = "152228"
SURFACE = "243540"
INK = "E8F0EA"
CORAL = "F07167"
SEA = "3D9B8F"
APRICOT = "E8A87C"
PERI = "8B9CF7"
DIM = "7D8B90"
FAIL = "C45C4A"
OK = SEA


def hx(name: str) -> str:
    return "#" + name
