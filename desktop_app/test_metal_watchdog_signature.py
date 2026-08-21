"""The Metal GPU-watchdog signature the Gemma-encode fallback arms on.

The mitigation (truncate LTX2_GEMMA_MAX_LENGTH and retry once) was correct and
present since v3.x, but its matcher only knew the `Timeout` spelling. macOS
picks the code by how it decided to kill you, and on an M2 Max / macOS 26.5.2 it
kills a long prompt-encode command buffer as `ImpactingInteractivity` instead —
so the fallback never armed, the render died with SIGABRT, and the error text
invited the user to file a bug about a retry that could not fire. Reported and
root-caused by @ybekocak in #59; #44 is the same failure with the other code.

The negative cases matter as much: OutOfMemory and InnocentVictim are different
failures. Arming a shorter-prompt retry on those burns a second render to reach
the same end.
"""

import re
import unittest
from pathlib import Path

PANEL = Path(__file__).with_name("mlx_ltx_panel.py")


def _panel_regex() -> re.Pattern:
    """The REAL pattern out of the panel source, so this cannot drift from it."""
    src = PANEL.read_text()
    i = src.index("_METAL_TIMEOUT_RX = re.compile(")
    body = src[i:src.index("re.IGNORECASE)", i)]
    parts = re.findall(r'r"([^"]+)"', body)
    assert parts, "could not extract the pattern from the panel source"
    return re.compile("".join(parts), re.IGNORECASE)


class MetalWatchdogSignature(unittest.TestCase):
    def setUp(self):
        self.rx = _panel_regex()

    def test_arms_on_impacting_interactivity(self):
        """#59 — M2 Max / macOS 26.5.2, the line that used to be missed."""
        line = ("libc++abi: terminating due to uncaught exception of type "
                "std::runtime_error: [METAL] Command buffer execution failed: "
                "Impacting Interactivity "
                "(0000000e:kIOGPUCommandBufferCallbackErrorImpactingInteractivity)")
        self.assertTrue(self.rx.search(line))

    def test_arms_on_classic_timeout(self):
        """#44 — M1 Max, the spelling that always worked. Must keep working."""
        line = ("[METAL] Command buffer execution failed: Caused GPU Timeout "
                "Error (00000002:kIOGPUCommandBufferCallbackErrorTimeout)")
        self.assertTrue(self.rx.search(line))

    def test_does_not_arm_on_out_of_memory(self):
        line = ("[METAL] Command buffer execution failed: Out Of Memory "
                "(00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)")
        self.assertFalse(self.rx.search(line))

    def test_does_not_arm_on_innocent_victim(self):
        line = "[METAL] failed: (00000004:kIOGPUCommandBufferCallbackErrorInnocentVictim)"
        self.assertFalse(self.rx.search(line))

    def test_ordinary_render_output_never_arms(self):
        for line in ("Denoising:  50%|#####     | 4/8 [00:21<00:21,  5.4s/it]",
                     "[Decoding video + audio + muxing] done in 15.6s",
                     "step:generate done"):
            self.assertFalse(self.rx.search(line), line)


if __name__ == "__main__":
    unittest.main()
