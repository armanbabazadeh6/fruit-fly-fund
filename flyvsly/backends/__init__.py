"""Signal backends. A backend maps an observed market frame to a trading proposal.

`neural` runs the vendored MaleCNS v1.0 engine. `procedural` is a labelled stand-in that
exists so the interface can be built and tested without several hundred megabytes of
connectome state, and it must never be presented as neural activity.
"""
