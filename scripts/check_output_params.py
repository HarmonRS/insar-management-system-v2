"""Check output parameters of each ENVI task."""
from envipyengine import Engine

TASK_NAMES = [
    "SARsInSARInterferogramGeneration",
    "SARsInSARFilterAndCoherence",
    "SARsInSARRemoveResidualPhaseFrequency",
    "SARsInSARPhaseUnwrapping",
    "SARsInSARRefinementAndReflattening",
    "SARsInSARPhaseToDisplacement",
]

engine = Engine("ENVI")
for name in TASK_NAMES:
    task = engine.task(name)
    params = task.parameters
    print(f"=== {name} ===")
    if isinstance(params, (list, tuple)):
        for p in params:
            if isinstance(p, dict) and p.get("direction") == "output":
                print(f"  [OUTPUT] {p.get('name')} ({p.get('type')})")
    print()
