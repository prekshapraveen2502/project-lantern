"""LANTERN course LLM client (Tool Tutorial Part 2, orientation slides, parts 1-4).

Every model and embedding call in your pipeline goes through this module.
Modes: replay (cache only, offline), record (call, then cache), live (call only).
LANTERN_LLM_MODE overrides llm.mode in params.yaml. The code below is the slides' code,
assembled in dependency order (part 1, part 3, part 2, part 4), plus a small adapter at the end.
"""
import hashlib, json, os, yaml
from pathlib import Path
P = yaml.safe_load(open("params.yaml"))
PROVIDER = P["llm"]["provider"]
CACHE = Path("data/llm_cache")
CACHE.mkdir(parents=True, exist_ok=True)
MODE = os.environ.get("LANTERN_LLM_MODE", P["llm"]["mode"])

def cache_key(provider, model, messages,
              temperature=0, max_tokens=1024, schema=None):
    payload = {"provider": provider, "model": model,
               "temperature": temperature,
               "max_tokens": max_tokens,
               "messages": messages, "schema": schema}
    canon = json.dumps(payload, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


LEDGER = "logs/cost_ledger.jsonl"
PRICES = P["llm"]["prices"]   # USD per 1M tokens: [in, out]
CAP = P["budget"]["usd_cap_total"]
class BudgetExceeded(RuntimeError): pass

def usd(model, tin, tout):
    p_in, p_out = PRICES[model]
    return (tin * p_in + tout * p_out) / 1e6

def spent():
    if not os.path.exists(LEDGER): return 0.0
    return sum(json.loads(l)["usd"] for l in open(LEDGER))

def budget_check(model, tin, tout=1024):  # before a live call
    if spent() + usd(model, tin, tout) > CAP:
        raise BudgetExceeded(f"{CAP} USD cap reached")

def ledger(model, tin, tout, stage):      # after a live call
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    row = {"model": model, "in": tin, "out": tout,
           "usd": round(usd(model, tin, tout), 6),
           "stage": stage}
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps(row) + "\n")


# INSTR: your instructor-wrapped provider client
def structured(response_model, messages, model,
               stage="build", **kw):
    schema = response_model.model_json_schema()
    key = cache_key(PROVIDER, model, messages,
                    schema=schema, **kw)
    f = CACHE / f"{key}.json"
    if f.exists():                          # hit
        return response_model.model_validate_json(
            f.read_text())
    if MODE == "replay":
        raise RuntimeError(f"cache miss {key}")
    # budget_check and ledger are defined in part 3
    budget_check(model, len(json.dumps(messages)) // 4)
    out, raw = INSTR.create_with_completion(
        response_model=response_model, model=model,
        messages=messages, **kw)
    if MODE == "record":          # cache before ledger,
        f.write_text(out.model_dump_json())  # so no re-pay
    u = raw.usage                 # names vary by provider
    ledger(model, u.prompt_tokens,
           u.completion_tokens, stage)
    return out


import numpy as np
EMB = Path("data/embed_cache")
EMB.mkdir(parents=True, exist_ok=True)

def embed(texts, model, compute):
    # compute(texts) -> vectors; runs only on a miss,
    # and never in replay
    vecs = []
    for t in texts:
        h = hashlib.sha256(f"{model}|{t}".encode())
        f = EMB / f"{h.hexdigest()}.npy"
        if f.exists():                        # hit
            vecs.append(np.load(f)); continue
        if MODE == "replay":
            raise RuntimeError(f"embedding cache miss {f.name}")
        v = np.asarray(compute([t])[0], dtype=np.float32)
        if MODE == "record":
            np.save(f, v)
        vecs.append(v)
    return np.vstack(vecs)


# ---------------------------------------------------------------------------
# Adapter (not on the slides): connects to a provider only when a live call is made,
# and normalizes token-usage field names so the code above stays provider-agnostic.
# Live and record modes need: pip install instructor openai   (or anthropic)
# plus OPENAI_API_KEY / ANTHROPIC_API_KEY in your environment. Replay needs neither.
from types import SimpleNamespace as _NS

class _LazyInstructor:
    _client = None
    def _connect(self):
        if self._client is None:
            import instructor
            if PROVIDER == "openai":
                from openai import OpenAI
                self._client = instructor.from_openai(OpenAI())
            elif PROVIDER == "anthropic":
                from anthropic import Anthropic
                self._client = instructor.from_anthropic(Anthropic())
            else:
                raise ValueError(f"unsupported provider {PROVIDER!r}")
        return self._client
    def create_with_completion(self, **kw):
        kw.setdefault("max_tokens", 1024)          # Anthropic requires it
        out, raw = self._connect().create_with_completion(**kw)
        u = getattr(raw, "usage", None)
        tin = getattr(u, "prompt_tokens", None) or getattr(u, "input_tokens", 0) or 0
        tout = getattr(u, "completion_tokens", None) or getattr(u, "output_tokens", 0) or 0
        return out, _NS(usage=_NS(prompt_tokens=tin, completion_tokens=tout))

INSTR = _LazyInstructor()

def PROVIDER_EMBED(texts, model=None):
    """Hosted embeddings (OpenAI). Called only on a cache miss in record or live mode."""
    from openai import OpenAI
    resp = OpenAI().embeddings.create(model=model or P["llm"]["embed"]["hosted"], input=list(texts))
    return [d.embedding for d in resp.data]
