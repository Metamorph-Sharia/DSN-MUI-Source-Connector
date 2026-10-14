"""DSN-MUI fatwa ingestion pipeline.

Minimal, deterministic fetch → parse → store pipeline. No reasoning, no LLM.
Just structured raw data behind a CLI contract (base.py / CONTRACT.md) so an
orchestrator can run DSN / OJK / BI connectors the same way.
"""

__version__ = "0.4.0"
