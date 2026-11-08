"""T3-S2: surface-code selection with the scaled score samples nothing."""

from __future__ import annotations

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import stim

    class NoSampling(stim.Circuit):
        def compile_sampler(self, *args, **kwargs):
            raise AssertionError("sampled")

        def compile_detector_sampler(self, *args, **kwargs):
            raise AssertionError("sampled")

        def compile_m2d_converter(self, *args, **kwargs):
            raise AssertionError("sampled")

    stim.Circuit = NoSampling

    import nove

    def refuse(*args, **kwargs):
        raise AssertionError("sampled")

    nove.Experiment.compile = refuse
    nove.Experiment.run = refuse

    from noise_aware_compilation.decoder_graph import matching_graph
    from noise_aware_compilation.devices import load_device
    from noise_aware_compilation.fast_lowering import Lowering
    from noise_aware_compilation.placement import _rounded, approximate_dem, enumerate_placements
    from noise_aware_compilation.scaled_score import ScaledParameters, scaled_score_from_graph
    from noise_aware_compilation.surface_code import hook_safe_schedules, surface_code

    try:
        stim.Circuit("H 0\\nM 0").compile_sampler()
    except AssertionError:
        pass
    else:
        raise SystemExit("the sampler mock is not in place")

    stored = load_device("miami")
    scores = []
    for schedule in hook_safe_schedules(3, 3, "CZ"):
        circuit = surface_code(3, 3, schedule)
        lowering = Lowering(circuit, stored.device, stored.noise)
        for m in enumerate_placements(circuit, stored.device):
            noisy = lowering.stim_circuit(m)
            assert isinstance(noisy, NoSampling)
            graph = matching_graph(approximate_dem(noisy, decompose=True))
            scores.append((_rounded(scaled_score_from_graph(graph, ScaledParameters(10, 0.05))), m))
    print(len(scores), min(scores)[1])
    """
)


def test_t3_s2_scaled_selection_samples_nothing() -> None:
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=600, check=False
    )
    assert done.returncode == 0, done.stderr[-2000:]
    count, _choice = done.stdout.split(maxsplit=1)
    assert int(count) == 2 * 176
