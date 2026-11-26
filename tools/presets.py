"""Asset presets for make3d / art3d: sensible triangle, texture and detail defaults per kind of asset.

Any option given explicitly (--tris, --tex, --resolution, --height, --template, --prop) overrides
the preset. Numbers are for PC/console games; halve tris and drop one texture size for mobile.

TRELLIS gives ~280k triangles and 2048 textures at resolution 1024 (props at 512: ~140k, 1024).
Resolution 1536 gave the same (272k, 2048) in ~14 min instead of 5-8, and the bake only resamples
the TRELLIS texture, so presets stop at 2048 textures: 4096 would be 2.5x the file size, no new detail.
"""

# kind: "character" (pose template + humanoid rig) or "prop" (no template, no rig)
PRESETS = {
    "hero":             dict(label="Main character", kind="character", tris=60000, tex=2048, resolution=1024,
                             note="Player character or close-up hero"),
    "side":             dict(label="Side character", kind="character", tris=30000, tex=2048, resolution=1024,
                             note="Companions, named characters"),
    "npc":              dict(label="NPC / crowd", kind="character", tris=12000, tex=1024, resolution=1024,
                             note="Townsfolk, guards, background people"),
    "monster":          dict(label="Monster (humanoid)", kind="character", tris=40000, tex=2048, resolution=1024,
                             height=2.2, template="a-pose-stocky",
                             note="Orcs, trolls, undead: two arms, two legs, gets the humanoid rig"),
    "creature":         dict(label="Creature (non-humanoid)", kind="prop", tris=30000, tex=2048, resolution=1024,
                             height=1.5, note="Beasts, dragons, slimes; no rig, the auto-rig is humanoid only"),
    "prop-simple":      dict(label="Simple object", kind="prop", tris=1500, tex=512, resolution=512, height=0.5,
                             note="Cups, books, rocks, crates"),
    "prop-complex":     dict(label="Complex object", kind="prop", tris=10000, tex=2048, resolution=1024, height=1.0,
                             note="Hero weapons, detailed furniture, machines"),
    "structure-simple": dict(label="Simple structure", kind="prop", tris=6000, tex=2048, resolution=1024, height=4.0,
                             note="Huts, walls, wells, market stalls"),
    "structure-complex": dict(label="Complex structure", kind="prop", tris=40000, tex=2048, resolution=1024,
                              height=10.0, note="Towers, temples, castle gates"),
}

DEFAULTS = dict(tris=30000, tex=2048)   # no preset
