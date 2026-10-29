# Motion-v3 attribution

The Python module running/motion_v3.py is adapted from:
https://github.com/LightWhite520/NekoSportsWorldTool/tree/bb52858b1c9273b7917819df58a029253e496422
(PR 134 to YanamiNeko/NekoSportsWorldTool).
Sources: crates/route-planner/src/motion.rs, biomech.rs and itinerary.rs.
Upstream attribution: YanamiNeko and contributors, including LightWhite520.

This derived module is CC BY-NC 4.0. See MOTION_V3_LICENSE.txt.
It is not covered by this repository's MIT grant. Existing MIT source retains
its original license. Keep this notice and the upstream license with copies.

Changes: Python standard-library RNG; tuple timeline API; subsecond queries;
stricter solver residual (1e-8 m target, 1e-7 m acceptance); geographic
integration adapter; centralized positive-half rounding. Parameters are
engineering defaults, not calibrated human measurements. Integration also
checks lateral acceleration at vertex crossings and rejects sharp-bend
overshoots from the reference's one-second look-ahead algorithm.

V3.2 modifications: bound every within-second vertex crossing, preserve braking
feasibility for nonnegative second samples, and smooth constraint response
for distance-solver convergence. The independent bend rejection remains.
