import sys
from pathlib import Path

from seed.anomalies import with_demo
from seed.generate import generate, write

path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/seed.db")
data, labels = with_demo(generate())
write(data, path)
print(f"wrote {path}: " + ", ".join(f"{len(v)} {k}" for k, v in data.items()))
print("demo holds: " + ", ".join(f"{l.request_id} ({l.anomaly})" for l in labels if l.anomaly))
