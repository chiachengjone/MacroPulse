"""
ingest.py — Bulk-ingest the knowledge_base/ PDFs into the macro-pulse-files index.

End-to-end, fully server-side:
  1. Creates an ingest pipeline `macropulse-ingest`:
       • attachment processor  → extracts PDF text (Apache Tika) into attachment.*
       • set processor         → overrides attachment.title with a clean title from manifest.tsv
       • inference processor   → embeds attachment.content into attachment.content_embedding
                                 using the .multilingual-e5-small-elasticsearch endpoint
  2. For each PDF: base64-encodes it and indexes one document through the pipeline.
     Document _id = filename (so re-running overwrites rather than duplicating).
  3. Verifies extracted-char count + embedding presence per file, then prints a summary.

No third-party dependencies (stdlib only). Reads ELASTIC_ENDPOINT / ELASTIC_API_KEY from .env.

Usage:
    python ingest.py            # ingest all PDFs in knowledge_base/
    python ingest.py --dry-run  # create pipeline + show plan, ingest nothing
"""

import base64
import glob
import json
import os
import sys
import time
import urllib.request
import urllib.error

# ── Config ──────────────────────────────────────────────────────────────────
KB_DIR = "knowledge_base"
INDEX = "macro-pulse-files"
PIPELINE = "macropulse-ingest"
EMBED_MODEL = ".multilingual-e5-small-elasticsearch"
DRY_RUN = "--dry-run" in sys.argv


def load_env(path: str = ".env") -> dict:
    """Minimal .env parser — avoids needing python-dotenv."""
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def load_titles(path: str = os.path.join(KB_DIR, "manifest.tsv")) -> dict:
    """filename -> clean human title, from the manifest written during download."""
    titles = {}
    if not os.path.exists(path):
        return titles
    with open(path) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                titles[parts[0]] = parts[1]
    return titles


def es(method: str, path: str, body: dict | None, ep: str, ak: str, timeout: int = 180):
    url = f"{ep}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"ApiKey {ak}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"error": e.read().decode()[:500]}


def main():
    env = load_env()
    ep = env.get("ELASTIC_ENDPOINT", "").rstrip("/")
    ak = env.get("ELASTIC_API_KEY", "")
    if not ep or not ak:
        sys.exit("ELASTIC_ENDPOINT / ELASTIC_API_KEY missing from .env")

    titles = load_titles()
    pdfs = sorted(glob.glob(os.path.join(KB_DIR, "*.pdf")))
    if not pdfs:
        sys.exit(f"No PDFs found in {KB_DIR}/")

    # ── 1. Create / update the ingest pipeline ──────────────────────────────
    pipeline_body = {
        "description": "Extract PDF text (Tika) + generate e5-small embeddings for macro docs",
        "processors": [
            {"attachment": {
                "field": "data",
                "target_field": "attachment",
                "indexed_chars": -1,          # extract full text (better BM25 recall)
                "remove_binary": True,        # don't store the base64 blob
            }},
            {"set": {                          # clean title from manifest beats Tika's PDF metadata
                "field": "attachment.title",
                "value": "{{{doc_title}}}",
                "if": "ctx.doc_title != null && ctx.doc_title != ''",
                "override": True,
            }},
            {"remove": {"field": "doc_title", "ignore_missing": True}},
            {"inference": {
                "model_id": EMBED_MODEL,
                "input_output": [{
                    "input_field": "attachment.content",
                    "output_field": "attachment.content_embedding",
                }],
                "on_failure": [
                    {"set": {"field": "embedding_error", "value": "{{_ingest.on_failure_message}}"}}
                ],
            }},
        ],
    }
    status, resp = es("PUT", f"/_ingest/pipeline/{PIPELINE}", pipeline_body, ep, ak)
    print(f"[pipeline] PUT {PIPELINE} -> {status} {resp}")
    if status >= 300:
        sys.exit("Failed to create pipeline; aborting.")

    print(f"\n{len(pdfs)} PDFs to ingest into '{INDEX}' via '{PIPELINE}'"
          + (" (DRY RUN — nothing indexed)\n" if DRY_RUN else "\n"))

    # ── 2. Ingest each PDF ──────────────────────────────────────────────────
    ok, failed = 0, 0
    for i, pdf in enumerate(pdfs, 1):
        fname = os.path.basename(pdf)
        doc_id = os.path.splitext(fname)[0]
        title = titles.get(fname, doc_id)

        if DRY_RUN:
            print(f"  [{i:>2}/{len(pdfs)}] would ingest {fname}  (id={doc_id})")
            continue

        with open(pdf, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        body = {"data": b64, "doc_title": title}
        # PUT with explicit _id + refresh so the verify count below is immediate
        status, resp = es(
            "PUT", f"/{INDEX}/_doc/{doc_id}?pipeline={PIPELINE}&refresh=wait_for",
            body, ep, ak, timeout=300,
        )

        if status < 300:
            # verify what landed
            _, got = es("GET", f"/{INDEX}/_doc/{doc_id}"
                        "?_source_includes=attachment.content_length,attachment.title"
                        "&_source_excludes=attachment.content,attachment.content_embedding",
                        None, ep, ak)
            att = got.get("_source", {}).get("attachment", {})
            clen = att.get("content_length", "?")
            err = got.get("_source", {}).get("embedding_error")
            flag = "  ⚠ EMBED FAIL: " + str(err) if err else ""
            print(f"  [{i:>2}/{len(pdfs)}] OK  {fname:<48} chars={clen}{flag}")
            ok += 1
        else:
            print(f"  [{i:>2}/{len(pdfs)}] FAIL {fname} -> {status} "
                  f"{json.dumps(resp)[:200]}")
            failed += 1
        time.sleep(0.2)  # gentle pacing

    if DRY_RUN:
        return

    # ── 3. Summary ──────────────────────────────────────────────────────────
    time.sleep(1)
    _, cnt = es("GET", f"/{INDEX}/_count", None, ep, ak)
    _, vec = es("POST", f"/{INDEX}/_count",
                {"query": {"exists": {"field": "attachment.content_embedding"}}}, ep, ak)
    print(f"\n── Done ── ingested OK={ok} failed={failed}")
    print(f"   index now holds {cnt.get('count')} docs; "
          f"{vec.get('count')} have embeddings.")


if __name__ == "__main__":
    main()