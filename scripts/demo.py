"""Upload included samples to a running Atlas workspace and print a cited answer."""

import argparse
import os
from pathlib import Path

import httpx

from app.evaluation import require, seed

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base-url", default="http://127.0.0.1:8000")
args = parser.parse_args()
headers = {"X-API-Key": os.environ["ATLAS_API_KEY"]} if os.environ.get("ATLAS_API_KEY") else {}
with httpx.Client(base_url=args.base_url, headers=headers, timeout=180) as client:
    ids = seed(client, Path(__file__).resolve().parents[1] / "samples")
    result = require(
        client.post(
            "/api/chat",
            json={"query": "How does the document ingestion pipeline work?", "document_ids": ids},
        )
    )
    print(result["answer"])
    for citation in result["citations"]:
        print(f"[{citation['label']}] {citation['filename']} — page {citation['page']}")
