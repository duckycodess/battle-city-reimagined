# Packs

A pack manifest names the levels of one pack in campaign order, together with the level
schema version those levels are written against and what the pack claims about rights in
its data. Manifests live here rather than in `levels/` so that tools which enumerate
level files never pick a manifest up as a level.

`classic.json` is the bundled regression pack: the three converted historical layouts.
Its licence field is `NOASSERTION` because the historical repository publishes no
licence; this repository asserts none over that layout data either.

Level paths are relative and must resolve inside the pack root. The bundled pack is
loaded with the package directory as its root, which is what lets it reach `levels/`;
`load_pack` defaults the root to the manifest's own directory for anything from outside
this repository.
