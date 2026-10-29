"""Download the optional local language model through a running Ollama server."""

import argparse
import json
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    for attempt in range(3):
        last = None
        try:
            with httpx.stream(
                "POST",
                args.base_url.rstrip("/") + "/api/pull",
                json={"model": args.model, "stream": True},
                timeout=300,
                trust_env=False,
            ) as response:
                response.raise_for_status()
                completed = False
                for line in response.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if event.get("error"):
                        raise RuntimeError(event["error"])
                    percent = (
                        int(event.get("completed", 0) * 100 / event["total"])
                        if event.get("total")
                        else None
                    )
                    marker = (event["status"], percent // 10 if percent is not None else None)
                    if marker != last:
                        print({"status": event["status"], "percent": percent}, flush=True)
                        last = marker
                    completed = event["status"] == "success"
                if not completed:
                    raise RuntimeError("Download ended without a success event")
            return
        except (httpx.HTTPError, RuntimeError):
            if attempt == 2:
                raise
            print("Model download interrupted; retrying the resumable download.", flush=True)
            time.sleep(2 ** (attempt + 1))


if __name__ == "__main__":
    main()
