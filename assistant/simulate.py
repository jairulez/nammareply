"""Chat with the assistant in the terminal. No network, nothing is sent.
  python3 -m assistant.simulate [client.json]
Prefix a line with 'owner:' to answer as the owner (e.g. 'owner: YES 1'), 'digest' for the summary."""
import sys, os, time
from .core import Assistant, load_client

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "clients", "demo-clinic.json")
    a = Assistant(load_client(path)); t0 = time.time()
    print("Simulated customer 'cust1' on whatsapp. Ctrl-D to quit.")
    try:
        while True:
            line = input("> ").strip()
            if line == "digest":
                print(a.digest(t0)); continue
            if line.startswith("owner:"):
                out = a.handle_owner_decision(line[6:]) or []
            else:
                out = a.handle_message("whatsapp", "cust1", line)
            for r in out:
                print(f"  [{r.kind} -> {r.to}] {r.text}")
    except EOFError:
        pass

if __name__ == "__main__":
    main()
