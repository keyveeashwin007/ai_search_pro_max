"""Command line: python -m txn_enrich.cli "SQ *BLUE BOTTLE #123 OAKLAND CA" [more ...]

Or pipe/read a file with one signature per line: python -m txn_enrich.cli -f sigs.txt
Use --fake to run offline without an API key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import demo_cache_path, load_settings
from .enricher import Enricher
from .inputs import load_transactions, parse_signatures
from .llm import FakeProvider, MissingAPIKeyError, OpenAIProvider

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrich merchant signatures.")
    parser.add_argument("signatures", nargs="*", help="merchant signatures")
    parser.add_argument("-f", "--file", help="file with one signature per line ('-' = stdin)")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--fake", action="store_true", help="offline fake provider")
    parser.add_argument("--json", action="store_true", help="print JSON rows")
    parser.add_argument(
        "--check", action="store_true",
        help="verify the API key and model (no tokens, no searches), then exit",
    )
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    settings = load_settings(args.config)
    settings = settings.with_overrides(
        cache_path=str(ROOT / settings.cache_path),
        known_merchants_path=str(ROOT / settings.known_merchants_path),
    )

    if args.check:
        return _check(settings.model, settings.search_context_size)

    txns = parse_signatures("\n".join(args.signatures))
    if args.file:
        if args.file == "-":
            extra = parse_signatures(sys.stdin.read())
        else:
            extra = load_transactions(Path(args.file).read_text(), args.file)
        typed_ids = {t.id for t in txns}
        for t in extra:
            if t.id in typed_ids:
                t.id = f"file-{t.id}"
        txns += extra
    if not txns:
        parser.error("no signatures given")

    if args.fake:  # separate cache: fake results must never be served to real runs
        settings = settings.with_overrides(cache_path=demo_cache_path(settings.cache_path))
        provider = FakeProvider()
    else:
        try:
            provider = OpenAIProvider(settings.model, settings.search_context_size)
        except MissingAPIKeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    result = Enricher(provider, settings).run(txns)

    if args.json:
        print(json.dumps({"rows": result.rows(), "metrics": result.metrics.to_dict()}, indent=2))
        return 0
    for r in result.records:
        e = r.enrichment
        print(
            f"{r.raw[:40]:<40} -> {e.merchant_name or '?':<24} {e.brand or '?':<18} "
            f"{e.category or '?':<24} "
            f"{r.confidence:.2f} [{r.path}]"
        )
    m = result.metrics
    print(
        f"\nrecords={m.records} unique={m.unique_merchants} cache={m.cache_hits} "
        f"rules={m.rule_hits} llm={m.llm_resolved} searches={m.web_searches} "
        f"(naive {m.naive_searches}) tokens in/out={m.input_tokens}/{m.output_tokens} "
        f"est=${m.est_cost_usd:.4f}"
        + (f" billed=${m.billed_usd:.6f}" if m.billed_calls else "")
    )
    dropped = getattr(provider, "dropped_features", None)
    if dropped:
        print(f"note: API rejected and the run skipped: {', '.join(sorted(dropped))}")
    return 0


def _check(model: str, context: str) -> int:
    try:
        provider = OpenAIProvider(model, context)
        model_id = provider.check()
    except MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # auth / model-not-found / network
        print(f"error: check failed for model {model!r}: {exc}", file=sys.stderr)
        return 1
    print(f"ok: key works on {provider.gateway} and model {model_id!r} is available")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
