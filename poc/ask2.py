"""ask2.py "<utterance>" [answer]: ask, answer a yes/no question if one comes, print what is said."""
import sys, time
from ovos_bus_client import MessageBusClient, Message
utt = sys.argv[1]; reply = sys.argv[2] if len(sys.argv) > 2 else None
bus = MessageBusClient(); bus.run_in_thread(); bus.connected_event.wait(10)
said, t0 = [], time.time()
def on_speak(m):
    d = m.data
    said.append(d)
    print(f"{time.time()-t0:5.1f}s speak [{(d.get('meta') or {}).get('dialog')}] {d.get('utterance')}", flush=True)
    if d.get("expect_response") and reply:
        time.sleep(1)
        print(f"{time.time()-t0:5.1f}s  -> answering {reply!r}", flush=True)
        bus.emit(Message("recognizer_loop:utterance", {"utterances": [reply], "lang": "en-us"}, {}))
bus.on("speak", on_speak)
bus.on("ovos.local_only.changed", lambda m: print(f"{time.time()-t0:5.1f}s local only -> {m.data.get('enabled')}", flush=True))
bus.on("ovos.network.request", lambda m: (m.data.get("blocked") or m.data.get("status") == 0) and print(f"{time.time()-t0:5.1f}s refused/failed: {m.data.get('host')} ({m.data.get('who')})", flush=True))
bus.emit(Message("recognizer_loop:utterance", {"utterances": [utt], "lang": "en-us"}, {}))
time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 25)
bus.close()
