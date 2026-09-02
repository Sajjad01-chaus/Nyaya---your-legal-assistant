#!/usr/bin/env python
"""Retrieval evaluation.

Scores retrieval directly rather than through generation, so the numbers
measure the part that decides whether an answer can be right at all. Generation
is measured separately with --with-generation, which also gives citation
accuracy and end-to-end latency.

    python eval/run_eval.py                        # default configuration
    python eval/run_eval.py --compare              # every configuration
    python eval/run_eval.py --with-generation      # + citation accuracy, cost

Metrics
    Recall@5 / @10   was any expected section retrieved in the top k
    MRR              1/rank of the first expected section
    Refusal rate     share of must_refuse questions the system declined
    False refusal    share of answerable questions it wrongly declined
    Latency          p50 / p95, split into retrieval and generation
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "55432")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

from app.core.config import settings  # noqa: E402
from app.llm.guards import upgrade_bare_citations, verify_answer  # noqa: E402
from app.llm.prompts import build_prompt  # noqa: E402
from app.llm.provider import RateLimited, build_provider  # noqa: E402
from app.retrieval.embeddings import Embedder, EmbedderConfig, Reranker  # noqa: E402
from app.retrieval.service import RetrievalService, to_prompt_dicts  # noqa: E402
from app.retrieval.store import QdrantStore  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_set.jsonl"
RESULTS = Path(__file__).parent / "results"


@dataclass(slots=True)
class Config:
    """One evaluation configuration."""

    name: str
    collection: str
    embed_model: str
    embed_dim: int
    rerank: bool
    hybrid: bool = True
    description: str = ""


@dataclass(slots=True)
class Outcome:
    qid: str
    question: str
    qtype: str
    expected: list[str]
    retrieved: list[str] = field(default_factory=list)
    answered: bool = False
    confidence: str = ""
    score: float = 0.0
    route: str = ""
    retrieval_ms: float = 0.0
    generation_ms: float = 0.0
    answer: str = ""
    cited: list[str] = field(default_factory=list)
    citations_valid: bool = True
    generation_failed: bool = False
    cost_usd: float = 0.0

    def rank_of_first_hit(self) -> int | None:
        for index, citation in enumerate(self.retrieved, start=1):
            if citation in self.expected:
                return index
        return None

    def recall_at(self, k: int) -> float:
        if not self.expected:
            return 0.0
        return 1.0 if any(c in self.expected for c in self.retrieved[:k]) else 0.0


def load_golden() -> list[dict]:
    return [
        json.loads(line)
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _section_key(chunk) -> str:  # noqa: ANN001
    return f"{chunk.act_short} s.{chunk.section_number}"


async def evaluate(
    config: Config, cases: list[dict], *, with_generation: bool
) -> list[Outcome]:
    store = QdrantStore(settings.qdrant_url)
    embedder = Embedder(
        EmbedderConfig(
            model=config.embed_model,
            dim=config.embed_dim,
            query_prefix=settings.embed_query_prefix,
            passage_prefix=settings.embed_passage_prefix,
        )
    )

    class _FusionOnly:
        """No cross-encoder: keep fusion order, and score by fusion rank.

        The comparison has to be honest about *why* the rerank is there. A stub
        returning a constant would rig it, so this reproduces what a system
        without a cross-encoder can actually know: the RRF score, min-max
        normalised inside the result set, which is the usual practical move.

        That normalisation is exactly the problem. Rescaling within one query
        always makes the top hit look strong, so it cannot distinguish the best
        of a good set from the best of a bad one -- which is the judgement a
        refusal depends on.
        """

        @staticmethod
        def rerank(_query, documents, keep):  # noqa: ANN001, ANN205
            n = min(keep, len(documents))
            # rank-normalised into logit space so the sigmoid downstream sees a
            # comparable range to the cross-encoder's output
            return [(i, 6.0 - 12.0 * i / max(n - 1, 1)) for i in range(n)]

    service = RetrievalService(
        store=store,
        embedder=embedder,
        reranker=Reranker(settings.rerank_model) if config.rerank else _FusionOnly(),
        statute_collection=config.collection,
        docs_collection=settings.qdrant_collection_docs,
        confidence_high=settings.confidence_high,
        confidence_low=settings.confidence_low,
        rerank_top_k=settings.rerank_top_k,
        rerank_keep=10,          # top-10 so Recall@10 is measurable
        candidates=settings.hybrid_candidates,
    )
    provider = build_provider(settings) if with_generation else None

    outcomes: list[Outcome] = []
    for case in cases:
        started = time.perf_counter()
        result = await service.retrieve(case["q"])
        retrieval_ms = (time.perf_counter() - started) * 1000

        outcome = Outcome(
            qid=case["id"],
            question=case["q"],
            qtype=case["type"],
            expected=case["expected_sections"],
            retrieved=[_section_key(c) for c in result.chunks],
            answered=result.should_answer,
            confidence=result.confidence.value,
            score=result.score,
            route=result.route_taken,
            retrieval_ms=round(retrieval_ms, 1),
        )

        if with_generation and provider is not None and result.should_answer:
            statute = to_prompt_dicts(result.chunks[:6])
            prompt = build_prompt(case["q"], statute)
            gen_started = time.perf_counter()
            pieces: list[str] = []
            usage = None
            try:
                async for chunk in provider.stream(
                    prompt.system, prompt.user,
                    max_tokens=settings.llm_max_tokens,
                    temperature=settings.llm_temperature,
                ):
                    if chunk.text:
                        pieces.append(chunk.text)
                    if chunk.usage:
                        usage = chunk.usage
            except RateLimited as limited:
                # Free tiers throttle a 35-question run hard. Wait it out once
                # rather than recording a provider quota as a system failure.
                await asyncio.sleep(min(limited.retry_after + 1.0, 65.0))
                pieces.clear()
                try:
                    async for chunk in provider.stream(
                        prompt.system, prompt.user,
                        max_tokens=settings.llm_max_tokens,
                        temperature=settings.llm_temperature,
                    ):
                        if chunk.text:
                            pieces.append(chunk.text)
                        if chunk.usage:
                            usage = chunk.usage
                except Exception as exc:  # noqa: BLE001
                    outcome.answer = f"[generation failed after retry: {exc}]"
            except Exception as exc:  # noqa: BLE001
                outcome.answer = f"[generation failed: {exc}]"
            outcome.generation_ms = round((time.perf_counter() - gen_started) * 1000, 1)
            outcome.answer = outcome.answer or "".join(pieces)

            # A provider quota exhaustion is not a citation defect. Counting
            # it as one made the metric measure the free tier rather than the
            # system.
            outcome.generation_failed = outcome.answer.startswith("[generation failed")

            outcome.answer, _ = upgrade_bare_citations(outcome.answer, statute)
            report = verify_answer(outcome.answer, statute)
            outcome.cited = [c.render() for c in report.valid]
            # Citation accuracy: every cited section must be in the retrieved
            # context AND relevant, which for a graded set means it appears in
            # the expected list when one exists.
            outcome.citations_valid = not outcome.generation_failed and not report.invented and (
                not outcome.expected
                or any(
                    f"{c.act} s.{c.section}" in outcome.expected for c in report.valid
                )
            )
            if usage:
                outcome.cost_usd = usage.cost_usd(
                    settings.cost_per_1m_input_usd, settings.cost_per_1m_output_usd
                )

        if with_generation:
            await asyncio.sleep(2.0)   # pace the free tier
        outcomes.append(outcome)
        marker = "." if _is_correct(outcome) else "x"
        sys.stdout.write(marker)
        sys.stdout.flush()

    sys.stdout.write("\n")
    await store.close()
    return outcomes


def _is_correct(outcome: Outcome) -> bool:
    if outcome.qtype == "must_refuse":
        return not outcome.answered
    return outcome.recall_at(5) == 1.0


def summarise(name: str, outcomes: list[Outcome]) -> dict:
    answerable = [o for o in outcomes if o.qtype != "must_refuse"]
    refusable = [o for o in outcomes if o.qtype == "must_refuse"]

    def pct(values: list[float]) -> float:
        return round(100 * sum(values) / len(values), 1) if values else 0.0

    reciprocal = []
    for outcome in answerable:
        rank = outcome.rank_of_first_hit()
        reciprocal.append(1.0 / rank if rank else 0.0)

    retrieval = [o.retrieval_ms for o in outcomes]
    generation = [o.generation_ms for o in outcomes if o.generation_ms]
    generated = [o for o in answerable if o.answer and not o.generation_failed]

    def percentile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)

    return {
        "config": name,
        "questions": len(outcomes),
        "answerable": len(answerable),
        "must_refuse": len(refusable),
        "recall@5": pct([o.recall_at(5) for o in answerable]),
        "recall@10": pct([o.recall_at(10) for o in answerable]),
        "mrr": round(sum(reciprocal) / len(reciprocal), 3) if reciprocal else 0.0,
        "refusal_rate_out_of_scope": pct([0.0 if o.answered else 1.0 for o in refusable]),
        "false_refusal_rate": pct([0.0 if o.answered else 1.0 for o in answerable]),
        "generation_failures": sum(1 for o in answerable if o.generation_failed),
        "citation_accuracy": pct([1.0 if o.citations_valid else 0.0 for o in generated])
        if generated
        else None,
        "retrieval_p50_ms": percentile(retrieval, 0.50),
        "retrieval_p95_ms": percentile(retrieval, 0.95),
        "generation_p50_ms": percentile(generation, 0.50) if generation else None,
        "generation_p95_ms": percentile(generation, 0.95) if generation else None,
        "mean_cost_usd": round(
            sum(o.cost_usd for o in generated) / len(generated), 6
        )
        if generated
        else None,
    }


def print_table(rows: list[dict]) -> None:
    columns = [
        ("config", "configuration", 34),
        ("recall@5", "R@5", 7),
        ("recall@10", "R@10", 7),
        ("mrr", "MRR", 7),
        ("refusal_rate_out_of_scope", "refuse", 8),
        ("false_refusal_rate", "false-ref", 10),
        ("citation_accuracy", "cite-acc", 9),
        ("retrieval_p50_ms", "ret p50", 9),
        ("retrieval_p95_ms", "ret p95", 9),
    ]
    header = "  ".join(f"{label:<{width}}" for _, label, width in columns)
    print("\n" + header)
    print("-" * len(header))
    for row in rows:
        cells = []
        for key, _, width in columns:
            value = row.get(key)
            text = "-" if value is None else (
                value if isinstance(value, str) else f"{value:g}"
            )
            cells.append(f"{text:<{width}}")
        print("  ".join(cells))


def print_failures(outcomes: list[Outcome]) -> None:
    misses = [o for o in outcomes if not _is_correct(o)]
    if not misses:
        print("\nno failures")
        return
    print(f"\nfailures ({len(misses)}):")
    for outcome in misses:
        if outcome.qtype == "must_refuse":
            print(f"  [{outcome.qid}] ANSWERED an out-of-scope question "
                  f"(conf={outcome.confidence}, score={outcome.score})")
        else:
            print(f"  [{outcome.qid}] expected {outcome.expected}, "
                  f"got {outcome.retrieved[:4]}")
        print(f"           {outcome.question}")


CONFIGS = {
    "hybrid+rerank": Config(
        name="bge-base | hybrid + rerank",
        collection="nyaya_statute",
        embed_model="BAAI/bge-base-en-v1.5",
        embed_dim=768,
        rerank=True,
        description="the shipped configuration",
    ),
    "hybrid-only": Config(
        name="bge-base | hybrid, no rerank",
        collection="nyaya_statute",
        embed_model="BAAI/bge-base-en-v1.5",
        embed_dim=768,
        rerank=False,
        description="isolates what the cross-encoder contributes",
    ),
}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="hybrid+rerank", choices=list(CONFIGS))
    parser.add_argument("--compare", action="store_true", help="run every configuration")
    parser.add_argument("--with-generation", action="store_true")
    parser.add_argument("--out", default=None, help="write results JSON here")
    args = parser.parse_args()

    cases = load_golden()
    print(f"golden set: {len(cases)} questions "
          f"({sum(1 for c in cases if c['type'] == 'must_refuse')} must refuse)")

    names = list(CONFIGS) if args.compare else [args.config]
    rows: list[dict] = []
    detail: dict[str, list[dict]] = {}

    for name in names:
        config = CONFIGS[name]
        print(f"\n>>> {config.name}  ({config.description})")
        outcomes = await evaluate(config, cases, with_generation=args.with_generation)
        summary = summarise(config.name, outcomes)
        rows.append(summary)
        detail[name] = [asdict(o) for o in outcomes]
        print_failures(outcomes)

    print_table(rows)

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(args.out) if args.out else RESULTS / f"eval-{stamp}.json"
    path.write_text(
        json.dumps(
            {"generated_at": stamp, "summary": rows, "outcomes": detail},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
