import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dream.memory.dream_memory import DreamMemory


class DreamMemoryTests(unittest.TestCase):
    def test_reinforcement_uses_focus_terms_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = DreamMemory(str(Path(tmpdir) / "dream.json"))
            memory.load_or_init("meteorology", ["Atmospheric pressure"])
            memory.set_research(
                query="meteorology atmospheric pressure",
                focus_terms=["Atmospheric pressure"],
                sources=[
                    {
                        "title": "Forecast Maps",
                        "url": "https://weather.gov/forecastmaps/",
                        "domain": "weather.gov",
                        "snippet": "Forecast maps summarize expected weather conditions across the United States.",
                    }
                ],
                candidate_statements=["Forecast maps summarize expected weather conditions across the United States."],
            )
            memory.record_cycle(
                cycle=1,
                score=1.0,
                passed=True,
                concept_gaps=[],
                weak_areas=[],
                n_statements_added=4,
            )
            memory.reinforce_cycle(
                score=1.0,
                passed=True,
                concept_gaps=[],
                weak_areas=[],
                n_statements_added=4,
                sources=memory.research_sources,
                focus_terms=memory.current_research.get("focus_terms", []),
            )

            self.assertEqual(memory.reinforcement_focus(1), ["Atmospheric pressure"])
            self.assertGreater(memory.concept_mastery["Atmospheric pressure"], 0.0)
            self.assertGreater(memory.source_rewards["weather.gov"], 0.0)
            summary = memory.summary()
            self.assertEqual(summary["research_sources"], 1)
            self.assertEqual(summary["reinforcement_focus"], ["Atmospheric pressure"])


if __name__ == "__main__":
    unittest.main()
