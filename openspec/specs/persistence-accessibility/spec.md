# Persistence and accessibility specification

## Persistence

Settings and campaign saves are local, versioned data. Validate input before migration, preserve a recoverable backup when changing schemas, and write through an atomic replace. Corrupt or newer-version saves must produce a clear recovery path and must not crash the simulation.

Keep network session secrets out of ordinary config and logs. No cloud account, sync, or economy is part of the first implementation.

## Accessibility

Support remappable keyboard and gamepad controls, configurable input repeat and sensitivity, readable UI scaling, contrast options, non-color-only indicators, reduced-motion effects, and independent sound controls. Keep feedback for damage, invincibility, team, base health, and stage state available through more than color alone.

Accessibility behavior must not alter simulation timing or competitive state. Mapping UI actions to tick inputs stays in the client adapter.

In v2 authored stages, each conveyor shows a fixed compass arrow and each paired pad a distinct link outline. All four directions and pads remain distinguishable in grayscale, without animation or color cues. The editor presents the same glyphs in its palette and stage preview. Accessibility cues do not modify fixed-tick simulation state.

## Acceptance

Settings and saves round-trip under current schemas. Invalid and older data have deterministic validation/migration behavior. Input remapping cannot issue actions outside protocol/game rules. A representative visual check documents contrast, scale, and reduced-motion behavior.
