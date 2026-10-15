"""The two Bedrock calls the pipeline makes.

Everything else in rag/ imports these two functions and nothing else talks to
Bedrock, so this file is the whole AWS surface of Track B.

Run the self-test when done (two tiny calls, well under a cent):

    python -m rag.bedrock --selftest

Both models were verified reachable from this account in Pre-Lab 3
(docs/bedrock-access-verification.txt), using exactly these IDs.
"""
from __future__ import annotations

import json
import time

import boto3
import numpy as np
from botocore.exceptions import ClientError

REGION = "us-east-1"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
# Cross-region inference profile. The bare "anthropic.claude-haiku-4-5-..." ID
# fails with ValidationException on-demand; Claude 4.5+ needs the "us." prefix.
GEN_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
EMBED_DIM = 1024

_rt = boto3.client("bedrock-runtime", region_name=REGION)

# Running totals so the eval runner can report what a run cost.
USAGE = {"embed_calls": 0, "embed_tokens": 0,
         "gen_calls": 0, "gen_input_tokens": 0, "gen_output_tokens": 0}


def _with_retry(fn, attempts: int = 5):
    """Run fn(); on a Bedrock ThrottlingException back off and retry
    (1, 2, 4, 8 s). Any other error surfaces immediately: an access or
    validation error will not fix itself by waiting."""
    for i in range(attempts):
        try:
            return fn()
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in ("ThrottlingException", "TooManyRequestsException") or i == attempts - 1:
                raise
            time.sleep(2 ** i)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed each string with Titan Text Embeddings V2.

    Returns a float32 array of shape (len(texts), EMBED_DIM), L2-normalised
    (ask Titan to normalise; then cosine similarity == dot product, which is
    what the FAISS inner-product index in rag/index.py relies on).

    Titan V2 embeds ONE text per request (no batch API), so loop. Each call:

        resp = _rt.invoke_model(
            modelId=EMBED_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text,
                             "dimensions": EMBED_DIM,
                             "normalize": True}),
        )
        payload = json.loads(resp["body"].read())
        vector = payload["embedding"]            # list of EMBED_DIM floats
        tokens = payload["inputTextTokenCount"]  # worth summing for the cost log

    Bedrock throttles bursts. Wrap the call so a ThrottlingException
    (botocore.exceptions.ClientError with
    e.response["Error"]["Code"] == "ThrottlingException") sleeps and retries
    a few times instead of killing a 48-chunk index build at chunk 40.

    The caller (rag/index.py) caches results on disk, so this runs once per
    corpus + once per query, not once per evaluation.
    """
    vectors = []
    for text in texts:
        body = json.dumps({"inputText": text, "dimensions": EMBED_DIM, "normalize": True})
        resp = _with_retry(lambda: _rt.invoke_model(
            modelId=EMBED_MODEL_ID, contentType="application/json",
            accept="application/json", body=body))
        payload = json.loads(resp["body"].read())
        vectors.append(payload["embedding"])
        USAGE["embed_calls"] += 1
        USAGE["embed_tokens"] += int(payload.get("inputTextTokenCount", 0))
    return np.asarray(vectors, dtype="float32")


def converse(system_prompt: str, user_prompt: str, *,
             max_tokens: int = 400, temperature: float = 0.2) -> dict:
    """Generate one completion with Claude Haiku 4.5 via the Converse API.

    Returns {"text": str, "input_tokens": int, "output_tokens": int,
             "stop_reason": str, "latency_ms": float}.

    The call:

        resp = _rt.converse(
            modelId=GEN_MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        text = resp["output"]["message"]["content"][0]["text"]
        usage = resp["usage"]          # {"inputTokens": n, "outputTokens": n, ...}
        stop = resp["stopReason"]      # "end_turn" normally; "max_tokens" = truncated

    Set temperature OR topP, never both: Claude 4+ on Bedrock rejects a request
    that carries both. Same throttling retry as embed_texts. Measure latency
    with time.perf_counter() around the call; the evidence file records it.
    """
    t0 = time.perf_counter()
    resp = _with_retry(lambda: _rt.converse(
        modelId=GEN_MODEL_ID,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    ))
    latency_ms = (time.perf_counter() - t0) * 1000
    usage = resp["usage"]
    USAGE["gen_calls"] += 1
    USAGE["gen_input_tokens"] += usage["inputTokens"]
    USAGE["gen_output_tokens"] += usage["outputTokens"]
    return {
        "text": resp["output"]["message"]["content"][0]["text"],
        "input_tokens": usage["inputTokens"],
        "output_tokens": usage["outputTokens"],
        "stop_reason": resp["stopReason"],
        "latency_ms": latency_ms,
    }


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        v = embed_texts(["30 day return policy", "free standard shipping at $50"])
        print(f"embed_texts: shape {v.shape}, dtype {v.dtype}, "
              f"norms {np.linalg.norm(v, axis=1).round(4).tolist()}, "
              f"cos(sim) between the two = {float(v[0] @ v[1]):.4f}")
        r = converse("You answer in one short sentence.", "What is 2 + 2?")
        print(f"converse: {r}")
    else:
        print(__doc__)
