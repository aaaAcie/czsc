from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Direction

symbol = 'sz002346'
sdt = '20180922'
edt = '20200908'

bars = research.get_raw_bars_origin(symbol, sdt=sdt, edt=edt)
engine = MooreCZSC(bars)

# Find the center that matches the user's #12 (lower rail 9.64)
# In my audit it was index 11
c = engine.all_centers[11]
print(f"Center Detail:")
print(f"  Start: {c.start_dt}")
print(f"  End: {c.end_dt}")
print(f"  Dir: {c.direction.name}")
print(f"  Lower: {c.lower_rail}")
print(f"  Upper: {c.upper_rail}")
print(f"  Confirm K: {c.confirm_k.dt if c.confirm_k else 'None'}")

# Check segments
print("\nSegments covering this time:")
for s in engine.segments:
    if s.start_k.dt <= c.confirm_k.dt <= s.end_k.dt:
        print(f"  Segment: {s.start_k.dt} -> {s.end_k.dt} | Dir: {s.direction.name}")

# Check ghost forks
print("\nGhost Forks Audit:")
for fork_base, consumed in engine.ghost_forks:
    print(f"Anchor: {fork_base.dt} ({fork_base.mark.name}) consumed:")
    for ctk in consumed:
        print(f"  - {ctk.dt} ({ctk.mark.name})")
        # Check if center confirm_k belongs to a swallowed segment
        # If confirm_k is between consumed[0] and consumed[1], it was produced by a ghost segment.
        if sorted([consumed[0].k_index, consumed[1].k_index])[0] <= c.confirm_k.k_index <= sorted([consumed[0].k_index, consumed[1].k_index])[1]:
             print(f"    *** CENTER WAS PRODUCED BY THIS GHOST SEGMENT ***")
