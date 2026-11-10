#!/usr/bin/env python3
"""E2 vs N2 workload benchmark, one JSON object per output line."""
import argparse
import glob
import json
import multiprocessing
import os
import statistics
import subprocess
import sys
import time

PY_LOOP = """
import time
t = time.perf_counter()
x = 0
for i in range(60_000_000):
    x = (x * 31 + i) % 1000003
print(time.perf_counter() - t)
"""

NUMPY_MATMUL = """
import time
import numpy as np
r = np.random.default_rng(0)
a, b = r.random((3000, 3000)), r.random((3000, 3000))
t = time.perf_counter()
a @ b
print(time.perf_counter() - t)
"""

PANDAS = """
import time
import numpy as np
import pandas as pd
r = np.random.default_rng(0)
n = 10_000_000
df = pd.DataFrame({"k": r.integers(0, 100_000, n), "v": r.random(n)})
keys = pd.DataFrame({"k": np.arange(100_000), "w": r.random(100_000)})
t = time.perf_counter()
df.groupby("k")["v"].agg(["mean", "sum", "std"])
df.merge(keys, on="k")
print(time.perf_counter() - t)
"""

R_MATMUL = 'set.seed(1); a <- matrix(runif(2000^2), 2000); cat(system.time(a %*% a)[["elapsed"]])'
R_LOOP = 's <- 0; cat(system.time(for (i in 1:50000000) s <- (s + i) %% 1000003)[["elapsed"]])'

ONE_THREAD = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
EIGHT_THREADS = {"OMP_NUM_THREADS": "8", "OPENBLAS_NUM_THREADS": "8", "MKL_NUM_THREADS": "8"}

SINGLE = [
    ("python_loop", [sys.executable, "-c", PY_LOOP], ONE_THREAD),
    ("numpy_1t", [sys.executable, "-c", NUMPY_MATMUL], ONE_THREAD),
    ("pandas", [sys.executable, "-c", PANDAS], ONE_THREAD),
    ("r_matmul", ["Rscript", "-e", R_MATMUL], ONE_THREAD),
    ("r_loop", ["Rscript", "-e", R_LOOP], ONE_THREAD),
]


def timed(cmd, env):
    out = subprocess.run(cmd, env={**os.environ, **env}, capture_output=True, text=True, check=True)
    return float(out.stdout.strip().splitlines()[-1])


def worker(_):
    return timed([sys.executable, "-c", PY_LOOP], ONE_THREAD)


def multicore():
    t = time.perf_counter()
    with multiprocessing.Pool(8) as pool:
        pool.map(worker, range(8))
    return time.perf_counter() - t


def cpu_info():
    fields = {}
    for line in subprocess.run(["lscpu"], capture_output=True, text=True, check=True).stdout.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return {"model": fields.get("Model name"), "vendor": fields.get("Vendor ID"), "nproc": os.cpu_count()}


def emit(**record):
    print(json.dumps(record), flush=True)


def full(reps, notebook_glob):
    emit(kind="cpu", **cpu_info())
    tasks = SINGLE + [("numpy_8t", [sys.executable, "-c", NUMPY_MATMUL], EIGHT_THREADS)]
    for name, cmd, env in tasks:
        runs = [timed(cmd, env) for _ in range(reps)]
        emit(kind="task", task=name, median=statistics.median(runs), runs=runs)
    runs = [multicore() for _ in range(reps)]
    emit(kind="task", task="multicore_8proc", median=statistics.median(runs), runs=runs)
    for nb in sorted(glob.glob(notebook_glob)):
        t = time.perf_counter()
        r = subprocess.run(
            ["jupyter", "nbconvert", "--to", "notebook", "--execute", "--output-dir", "/tmp/nb-out",
             "--ExecutePreprocessor.timeout=900", nb],
            capture_output=True, text=True, cwd=os.path.dirname(nb))
        emit(kind="notebook", notebook=os.path.basename(nb), seconds=time.perf_counter() - t,
             returncode=r.returncode, error=r.stderr[-300:] if r.returncode else "")


def soak(iterations, interval):
    emit(kind="cpu", **cpu_info())
    for i in range(iterations):
        start = time.time()
        for name, cmd, env in SINGLE:
            emit(kind="soak", iteration=i, ts=start, task=name, seconds=timed(cmd, env))
        time.sleep(max(0, interval - (time.time() - start)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)
    f = sub.add_parser("full")
    f.add_argument("--reps", type=int, default=5)
    f.add_argument("--notebooks", default="/bench-notebooks/*.ipynb")
    s = sub.add_parser("soak")
    s.add_argument("--iterations", type=int, default=96)
    s.add_argument("--interval", type=int, default=900)
    args = ap.parse_args()
    if args.mode == "full":
        full(args.reps, args.notebooks)
    else:
        soak(args.iterations, args.interval)


if __name__ == "__main__":
    main()
