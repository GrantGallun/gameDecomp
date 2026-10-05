"""Capture and gate the exact candidate's pre-as1 view, without reselecting it."""
from pathlib import Path
import phase_probe
import gate_phases

HERE = Path(__file__).resolve().parent / 'winning-phase'
if __name__ == '__main__':
    HERE.mkdir(exist_ok=False)
    phase_probe.OLD = Path('/home/grant/decomp/experiments/direct-compiler-20260926/probes')
    phase_probe.OUT = Path('/home/grant/decomp/experiments/direct-compiler-20260926/winning-phase')
    phase_probe.HERE = HERE
    phase_probe.SOURCES = {'winner': (157781, 'c6ae0473748e7e89e025dbcdf92f4ca6d9babbe6bb6b28dd5dce678e0bee9d13')}
    phase_probe.main()
    gate_phases.HERE = HERE
    gate_phases.OLD = phase_probe.OLD / 'repo/nonmatchings/drawControllerPakFileDeleteConfirmOptions'
    gate_phases.TAGS = {'winner': 'drawControllerPakFileDeleteConfirmOptions_direct_defaults_in_arms'}
    gate_phases.main()
