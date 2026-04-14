"""Verify envipyengine task names for the 6-step custom D-InSAR workflow.

Run:
    python scripts/verify_envi_tasks.py
"""
import sys

TASK_NAMES = [
    "SARsInSARInterferogramGeneration",
    "SARsInSARFilterAndCoherence",
    "SARsInSARRemoveResidualPhaseFrequency",
    "SARsInSARPhaseUnwrapping",
    "SARsInSARRefinementAndReflattening",
    "SARsInSARPhaseToDisplacement",
    # metatask (already working)
    "SARsMetataskInSARDisplacementGeneration",
]


def main():
    try:
        from envipyengine import Engine
    except ImportError:
        print("[ERROR] envipyengine not installed")
        return 1

    engine = Engine("ENVI")
    ok = 0
    fail = 0

    for name in TASK_NAMES:
        try:
            task = engine.task(name)
            params = task.parameters
            param_names = list(params.keys()) if isinstance(params, dict) else str(params)
            print(f"[OK] {name}")
            print(f"     params: {param_names}")
            print()
            ok += 1
        except Exception as exc:
            print(f"[FAIL] {name}: {exc}")
            print()
            fail += 1

    print(f"--- Result: {ok} ok, {fail} fail ---")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
