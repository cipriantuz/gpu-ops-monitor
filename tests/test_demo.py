"""The demo scenario end to end, on a fake clock: auto-repair -> LLM agent (the real demo_agent.py subprocess) ->
page the owner. It runs in a few seconds instead of two minutes."""
import demo
import monitor as m


def test_demo_goes_through_all_three_tiers(tmp_path, monkeypatch):
    t = {"now": 1_800_000_000.0}
    monkeypatch.setattr(m.time, "time", lambda: t["now"])
    monkeypatch.setenv("MONITOR_DIR", str(tmp_path))  # restored after the test (Demo sets it again)
    monkeypatch.setenv("DEMO_FIX_DIR", str(tmp_path))
    monkeypatch.setenv("DEMO_AGENT_DELAY", "0")
    sent = []
    monkeypatch.setattr(m, "send_alert", lambda title, message, priority, tags: sent.append((priority, title)))
    lines = []
    d = demo.Demo(tmp_path, out=lines.append)
    while d.n <= demo.LAST_ROUND:
        d.step()
        if d.monitor.agent and d.monitor.agent[0].poll() is None:
            d.monitor.agent[0].wait(timeout=60)
        t["now"] += demo.ROUND_S

    assert sent == [(2, "Auto-repaired: Inference server (ComfyUI :8188) (down 0 min)"),
                    (5, "Problem: GPU box (SSH + GPU)"),
                    (2, "Working again: GPU box (SSH + GPU)")]
    kinds = [(e["kind"], e["name"].split(" (")[0]) for e in d.monitor.events]
    assert kinds[:2] == [("repair", "Inference server"), ("repaired", "Inference server")]
    assert ("agent", "TTS server") in kinds and ("resolved", "TTS server") in kinds
    assert kinds.index(("agent", "GPU box")) < kinds.index(("error", "GPU box")) < kinds.index(("resolved", "GPU box"))
    assert all(e["kind"] != "repair" or "refused" in e["detail"] for e in d.monitor.events if "TTS" in e["name"])
    logs = sorted((tmp_path / "agent").glob("*.log"))
    assert len(logs) == 2
    assert "stopped it and started the task again" in logs[0].read_text(encoding="utf-8")
    assert "needs hands on the hardware" in logs[1].read_text(encoding="utf-8")
    page = d.monitor.page()
    assert "Maintenance: driver update (demo)" in page and "All good" in page
