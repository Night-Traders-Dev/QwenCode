import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dream.config import DreamConfig
from dream.memory.dream_memory import DreamMemory
from dream.phases import _stored_research_packet
from memory.store import MemoryStore


class MemoryStoreResearchTests(unittest.TestCase):
    def test_list_knowledge_filters_by_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(backend="file")
            store._data_dir = Path(tmpdir)
            store._data_dir.mkdir(parents=True, exist_ok=True)

            store.upsert_knowledge(
                key="dream:source:one",
                content=json.dumps(
                    {
                        "topic": "meteorology",
                        "query": "meteorology fronts",
                        "title": "Forecast Maps",
                        "url": "https://weather.gov/forecastmaps/",
                        "domain": "weather.gov",
                        "snippet": "National forecast maps summarize pressure, precipitation, and fronts.",
                    }
                ),
                source="weather.gov",
                category="dream_source",
                metadata={"topic": "meteorology"},
            )
            store.upsert_knowledge(
                key="dream:source:two",
                content=json.dumps(
                    {
                        "topic": "botany",
                        "query": "botany leaves",
                        "title": "Plant Guide",
                        "url": "https://example.edu/plants",
                        "domain": "example.edu",
                        "snippet": "Plants use leaves for photosynthesis.",
                    }
                ),
                source="example.edu",
                category="dream_source",
                metadata={"topic": "botany"},
            )

            rows = store.list_knowledge(category="dream_source", metadata={"topic": "meteorology"})
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source"], "weather.gov")

            memory = DreamMemory(str(Path(tmpdir) / "dream.json"))
            memory.load_or_init("meteorology", ["Weather fronts"])
            cfg = DreamConfig()
            cfg.research_max_sources = 3
            packet = _stored_research_packet("meteorology", memory, cfg, store)

            self.assertIsNotNone(packet)
            assert packet is not None
            self.assertEqual(len(packet.sources), 1)
            self.assertEqual(packet.sources[0].domain, "weather.gov")
            self.assertGreaterEqual(len(packet.candidate_statements), 1)


if __name__ == "__main__":
    unittest.main()
