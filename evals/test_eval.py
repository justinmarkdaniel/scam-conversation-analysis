"""Eval for the probabilistic scorer — you can't unit-assert an LLM float, so
measure it directionally instead. Off by default (`-m eval`); run against the
real model with LLM_PROVIDER=real to get a real number, or offline against the
FakeProvider to exercise the harness.

    pytest -m eval evals/
"""
import json
import pathlib

import pytest

from app.llm import get_provider

pytestmark = pytest.mark.eval

CASES = [
    json.loads(line)
    for line in (pathlib.Path(__file__).parent / "cases.jsonl").read_text().splitlines()
    if line.strip()
]
THRESHOLD = 0.5


def test_directional_accuracy(capsys):
    provider = get_provider()
    tp = fp = tn = fn = 0
    for case in CASES:
        prob = provider.score(case["text"]).probability
        predicted_fraud = prob >= THRESHOLD
        actual_fraud = case["label"] == "fraud"
        if actual_fraud and predicted_fraud:
            tp += 1
        elif actual_fraud and not predicted_fraud:
            fn += 1
        elif not actual_fraud and predicted_fraud:
            fp += 1
        else:
            tn += 1

    total = len(CASES)
    accuracy = (tp + tn) / total
    with capsys.disabled():
        print(f"\neval: n={total} accuracy={accuracy:.2f} "
              f"false_positives={fp} false_negatives={fn}")
    assert accuracy >= 0.7  # headroom for real-model wobble; tighten per model
