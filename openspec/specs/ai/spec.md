# AI specification

Bots choose legal inputs for the same shared deterministic simulation used by people. Difficulty profiles tune reaction delay, planning horizon, target selection, aim error, aggression, and coordination within explicit ranges. Difficulty may not bypass collision, visibility, rate limits, or game rules.

Given the same bot profile, seed, simulation state, and tick history, a bot must emit the same inputs. Bot randomness uses the simulation's seeded/versioned random source. AI decisions run at declared simulation ticks, not render frames or wall-clock intervals.

For explicitly v2 gimmick terrain, prediction accounts for a start-center-cell conveyor push and a valid, unoccupied paired-pad arrival after movement; an occupied exit is not a guaranteed shortcut. The existing bounded roam/clearance policy, not a new global path planner, supplies legal tick inputs. On gimmick-free grids bot decisions stay byte-identical for the same state, seed and history.

Start with reproducible single-tank behaviors and profiles. Team coordination and player-style variation need a proposal with fairness and performance acceptance criteria. Multiplayer servers may run approved bots authoritatively; clients cannot claim bot outcomes.
