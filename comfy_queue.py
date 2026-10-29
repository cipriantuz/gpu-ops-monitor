"""Save and restore the queue of a ComfyUI server (stdlib only).

When ComfyUI's queue worker thread dies, the server still answers HTTP but runs nothing. The queue lives only in
memory, so a restart loses it. Save the queue, restart the server (stop the PID listening on its port), then restore
it with the same prompt_id values (ComfyUI accepts prompt_id in POST /prompt): clients waiting on
/history/<prompt_id> reattach by themselves, with no duplicates.

    python comfy_queue.py save http://127.0.0.1:8188 queue.json
    python comfy_queue.py restore http://127.0.0.1:8188 queue.json

A job is skipped if its prompt_id is already queued or in the history, if its extra_data[DEDUP_KEY] is already
queued, or if one of its output filename prefixes is already used by a queued job (it was resubmitted meanwhile).
"""
import json
import sys
import urllib.request

DEDUP_KEY = "job_uuid"  # an id your submitting client puts in extra_data, if it has one


def request(base: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def prefixes(prompt: dict) -> set[str]:
    return {str(n["inputs"]["filename_prefix"]) for n in prompt.values()
            if isinstance(n, dict) and "filename_prefix" in n.get("inputs", {})}


def save(base: str, path: str) -> None:
    queue = request(base, "/queue")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(queue, f)
    print(f"saved {path}: {len(queue['queue_running'])} running, {len(queue['queue_pending'])} pending")


def restore(base: str, path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    # execution order: the running job, then the pending ones by number (a job sent to the front has a negative one)
    jobs = saved["queue_running"] + sorted(saved["queue_pending"], key=lambda j: j[0])
    queue = request(base, "/queue")
    now = queue["queue_running"] + queue["queue_pending"]
    keys = {j[3].get(DEDUP_KEY) for j in now if len(j) > 3 and isinstance(j[3], dict)} - {None}
    ids = {j[1] for j in now} | set(request(base, "/history?max_items=1000"))
    used = set().union(*(prefixes(j[2]) for j in now))
    sent = []
    for number, pid, prompt, extra, *_ in jobs:
        extra = dict(extra)
        if pid in ids or extra.get(DEDUP_KEY) in keys or prefixes(prompt) & used:
            print("skip", number, pid, "(already queued or in the history)")
            continue
        extra.pop("create_time", None)  # the server sets it
        body = {"prompt": prompt, "prompt_id": pid, "client_id": extra.get("client_id"), "extra_data": extra}
        r = request(base, "/prompt", body)
        sent.append(body)
        print("restored", number, "->", r.get("number"), r.get("prompt_id"), "errors:", list(r.get("node_errors") or {}))
    return sent


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("save", "restore"):
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"save": save, "restore": restore}[sys.argv[1]](sys.argv[2].rstrip("/"), sys.argv[3])
