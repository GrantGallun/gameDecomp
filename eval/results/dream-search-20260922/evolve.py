"""Second development cycle, including the observed greedy-search regression.

This deliberately retires Wobble from fresh evaluation into calibration. Freeze
the selected policy before evaluating three different exposed development states.
No previous world is silently upgraded to the strengthened context contract.

python eval/results/dream-search-20260922/evolve.py --run-id evolution-v1 --budget 32
"""
import pilot

pilot.CALIBRATION = ["Fdistort", "__MusIntProcessWobble"]
pilot.FRESH = ["__osDequeueThread", "osCreateViManager", "drawCharacterSelectSelectedCharacterTokens"]

if __name__ == "__main__":
    pilot.main()
