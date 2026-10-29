"""A stand-in for the LLM ops agent in demo mode (no model, no network).

The monitor starts it exactly like a real agent command: the escalation text arrives as the last argument and the
output goes to the agent log in the data directory. It "fixes" the hung TTS server and cannot fix a machine that is
off the network, which is what sends the demo to the owner.
"""
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # the log file, not a console: keep it UTF-8
prompt = sys.argv[1] if len(sys.argv) > 1 else ""
print("demo agent: the escalation says:")
print(prompt.split("\n\n")[1] if "\n\n" in prompt else prompt[:300])
time.sleep(float(os.environ.get("DEMO_AGENT_DELAY", "3")))
if "TTS server" in prompt:
    print("demo agent: the TTS process holds port 8191 but does not answer; stopped it and started the task again")
    (Path(os.environ["DEMO_FIX_DIR"]) / "tts_fixed").write_text("fixed by the demo agent\n", encoding="utf-8")
    sys.exit(0)
print("demo agent: the host does not answer on any route; this needs hands on the hardware")
sys.exit(1)
