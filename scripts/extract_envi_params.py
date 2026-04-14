"""Extract parameter names for each ENVI task (input params only)."""
import sys

TASK_NAMES = [
    "SARsInSARInterferogramGeneration",
    "SARsInSARFilterAndCoherence",
    "SARsInSARRemoveResidualPhaseFrequency",
    "SARsInSARPhaseUnwrapping",
    "SARsInSARRefinementAndReflattening",
    "SARsInSARPhaseToDisplacement",
]

def main():
    from envipyengine import Engine
    engine = Engine("ENVI")

    for name in TASK_NAMES:
        task = engine.task(name)
        params = task.parameters
        print(f"=== {name} ===")
        if isinstance(params, (list, tuple)):
            for p in params:
                if isinstance(p, dict):
                    direction = p.get("direction", "?")
                    pname = p.get("name", "?")
                    ptype = p.get("type", "?")
                    required = p.get("required", False)
                    req_tag = " [REQUIRED]" if required else ""
                    if direction == "input":
                        print(f"  {pname} ({ptype}){req_tag}")
        print()

if __name__ == "__main__":
    main()
