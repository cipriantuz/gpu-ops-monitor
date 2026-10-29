"""Demo mode: the real engine with simulated probes and scripted failures, sped up (one round = 2 s).

    python monitor.py demo            # dashboard on http://127.0.0.1:8198, about 2 minutes of scenario

Timeline (round numbers):
  r5   the inference server crashes -> auto-repair at the 2nd bad round -> back up -> "Auto-repaired" (nobody paged)
  r14  the TTS server hangs: the repair refuses (the port is still held) -> the LLM agent (demo_agent.py) fixes it
  r30  the GPU box drops off the network: no safe repair, the agent cannot fix hardware -> the owner is paged
  r60  the GPU box is back -> "Working again" to the owner
The thresholds are the real ones divided by 30 (agent after 10 s instead of 5 min, owner 30 s after the agent).
Notifications go to the console; nothing leaves the machine.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import monitor as m

ROUND_S = 2.0
LAST_ROUND = 64
DEMO_CONFIG = {
    "monitor": {"interval_s": ROUND_S, "dashboard_url": "http://127.0.0.1:8198/"},
    "notify": {"kind": "console"},
    "escalation": {"bad_rounds_to_alert": 2, "repairs_per_hour": 3, "repair_pause_s": 6,
                   "agent_after_error_s": 10, "agent_after_warning_s": 30, "owner_after_agent_s": 30},
    "agent": {"command": [sys.executable, str(Path(__file__).with_name("demo_agent.py")), "{prompt}"],
              "max_runs_per_day": 6, "max_runtime_min": 1},
}
STORY = {
    5: "simulating: the inference server crashes",
    14: "simulating: the TTS server hangs (process alive, port held, no answers)",
    30: "simulating: the GPU box drops off the network",
    60: "simulating: the GPU box is back",
}


class Demo:
    """Simulated probes driven by the round number; the engine (Tracker, repairs, agent, notifications) is real."""

    def __init__(self, data: Path, out=print) -> None:
        self.data, self.out, self.n = data, out, 0
        self.inference_up_at: int | None = None
        os.environ["MONITOR_DIR"] = str(data)
        os.environ["DEMO_FIX_DIR"] = str(data)
        m.apply_config(DEMO_CONFIG)
        m.CHECKS = self.checks()
        m.write_atomic(m.maintenance_file(), {"share_box": {"since": time.time(), "until": time.time() + 3600,
                                                            "reason": "driver update (demo)"}})
        self.monitor = m.Monitor()
        self.monitor.on_event = self.print_event

    # ---- probes
    def inference(self) -> tuple[str, str]:
        if 5 <= self.n and (self.inference_up_at is None or self.n < self.inference_up_at):
            return "error", "not responding (connection refused)"
        return "ok", "queue: 1 running, 2 pending · VRAM free 3.1/16.0 GB · current job for 1 min"

    def repair_inference(self) -> str:
        self.inference_up_at = self.n + 1  # the simulated service needs one round to come up
        return "started the ComfyUI task"

    def tts(self) -> tuple[str, str]:
        if self.n >= 14 and not (self.data / "tts_fixed").exists():
            return "error", "not responding (timeout)"
        return "ok", "12 items"

    def repair_tts(self) -> str:
        raise RuntimeError("listening on 8191 (PID 4242) but not answering: not killing it on my own")

    def gpu_box(self) -> tuple[str, str]:
        if 30 <= self.n < 60:
            return "error", "not answering over SSH (primary: timeout; fallback: timeout)"
        return "ok", "52 °C · 31/115 W · 4% · VRAM 1.2/16.0 GB · via primary"

    def checks(self) -> list[m.Check]:
        steady = lambda detail: (lambda: ("ok", detail))  # noqa: E731
        return [
            m.Check("comfy_local", "Workstation", "Inference server (ComfyUI :8188)", self.inference,
                    "Restart it with the ComfyUI scheduled task.", self.repair_inference),
            m.Check("tts_local", "Workstation", "TTS server (:8191)", self.tts,
                    "Find the hung process on 8191, stop it, start the TTS-Server task.", self.repair_tts),
            m.Check("gpu_local", "Workstation", "GPU", steady("61 °C · 180/300 W · 72% · VRAM 12.9/16.0 GB"),
                    "nvidia-smi: see what runs."),
            m.Check("disk_local", "Workstation", "Disks", steady("free: C 212 GB · D 1404 GB"), "Free some space."),
            m.Check("ram_local", "Workstation", "RAM", steady("free 18.4 of 64 GB"), "See what holds the RAM."),
            m.Check("gpu_box", "GPU box", "GPU box (SSH + GPU)", self.gpu_box,
                    "Is it powered on and on the network? Check the cable / Wi-Fi, then the power."),
            m.Check("share_box", "GPU box", "GPU box shares", steady("free: renders 820 GB"), "Reconnect the share."),
            m.Check("backup", "Nightly", "Backup", steady("last successful backup 03:31, 41.2 GB"), "See the backup log."),
        ]

    # ---- output
    def stamp(self) -> str:
        return f"[r{self.n:02d} {self.n * ROUND_S:5.0f}s]"

    def print_event(self, ev: dict) -> None:
        who = " -> owner" if ev.get("to") == "owner" else ""
        self.out(f"{self.stamp()} event {ev['kind']}{who}: {ev['name']} · {ev['detail']}")

    def step(self) -> None:
        if self.n in STORY:
            self.out(f"{self.stamp()} {STORY[self.n]}")
        self.monitor.round()
        self.n += 1


def main(port: int = 8198, wait: bool = True) -> int:
    data = Path(tempfile.mkdtemp(prefix="gpu-ops-demo-"))
    demo = Demo(data)
    original_send = m.send_alert

    def send(title: str, message: str, priority: int, tags: list[str]) -> None:
        demo.out(f"{demo.stamp()} NOTIFY p{priority}: {title}")
        for line in message.splitlines():
            if line.strip():
                demo.out(f"{'':14}| {line}")
        return None

    m.send_alert = send
    try:
        server = m.create_server(demo.monitor, port, "127.0.0.1")
    except OSError as e:
        print(f"port {port} is taken ({m.reason(e)}); try `demo --port 8199`")
        return 1
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"demo: dashboard http://127.0.0.1:{port}/ · data {data} · one round = {ROUND_S:g} s, "
          f"{LAST_ROUND} rounds", flush=True)
    try:
        while demo.n <= LAST_ROUND:
            start = time.monotonic()
            demo.step()
            time.sleep(max(0.0, ROUND_S - (time.monotonic() - start)))
        print("demo: scenario finished. Events:", data / "events.jsonl", flush=True)
        if wait:
            print("demo: the dashboard stays up; Ctrl+C to exit", flush=True)
            while True:
                time.sleep(ROUND_S)
                demo.step()
    except KeyboardInterrupt:
        pass
    finally:
        m.send_alert = original_send
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
