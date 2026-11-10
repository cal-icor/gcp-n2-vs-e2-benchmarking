# E2 vs N2 for JupyterHub user nodes on GKE

Cal-ICOR, November 2026. Tests ran on 2026-11-09 and 2026-11-10 in us-central1.

## Summary

- E2 fails as a primary user node. Single-threaded Python, numpy, pandas and R ran 1.2x to 2.6x slower on E2 than on N2, and our limit was 1.30x.
- E2 works as a fallback during N2 stockouts. It stayed within our limits on node startup, median image pull time and 8-process throughput.
- E2 run times varied more over 24 hours. On pandas, E2's p95/p50 reached 1.39, against 1.02 on N2.
- We'll keep N2 as the primary user pool until we're ready to move to N4.

## Contents

- [Summary](#summary)
- [Why E2](#why-e2)
- [Test setup](#test-setup)
- [Tests and pass criteria](#tests-and-pass-criteria)
- [Results](#results)
  - [Node ready](#node-ready)
  - [Cold start](#cold-start)
  - [Workloads](#workloads)
  - [24-hour soak](#24-hour-soak)
  - [Why the workload and soak gaps differ](#why-the-workload-and-soak-gaps-differ)
- [Limitations](#limitations)
- [What we're doing next](#what-were-doing-next)
- [Questions for Google Cloud](#questions-for-google-cloud)
- [Reproduce the tests](#reproduce-the-tests)

## Why E2

Our user nodes are `n2-highmem-8`. Since June 2026 we've hit stockouts of that machine type in our region. `e2-highmem-8` has the same vCPU count and memory and costs 31% less, so we tested whether it could replace N2 or back it up.

| | n2-highmem-8 | e2-highmem-8 |
|---|---|---|
| vCPU / memory | 8 / 64 GB | 8 / 64 GB |
| On-demand list price, us-central1 | \$0.5241/h | \$0.3616/h |
| CPU platform | Intel, fixed per series | Intel or AMD, chosen by Google when the VM is created |
| Resource model | Dedicated | Dynamic resource management |
| Published clock speed | Yes | No |

Our user nodes sit idle on CPU most of the day. From 2026-08-24 to 2026-11-06 they averaged 9% CPU busy, with a p99 of 36%. We left out each node's first 20 minutes, when image pulls keep the CPU busy. Pod count (110 per node) and memory requests fill a node before CPU does, so an E2 node holds as many users as an N2 node.

Slower cores could still hurt in two places:

1. A new node has to pull and extract every user image before it can host users.
2. A student running heavy single-threaded code waits longer for each cell.

## Test setup

We created two temporary node pools in our production cluster, one `e2-highmem-8` and one `n2-highmem-8`. Both copied our live user pool's settings and differed only in machine type:

- GKE 1.35.8-gke.1796000, Container-Optimized OS with containerd, 200 GB pd-balanced boot disk
- Four zones, autoscaling from 0 to 1 node
- kubelet `maxParallelImagePulls: 3`, CPU CFS quota off, 110 pods per node
- A `bench=true:NoSchedule` taint and a `bench=<family>` label, which kept user pods, placeholder pods and image-puller DaemonSets off the test nodes

Every test ran on a node the autoscaler created for it. The E2 nodes in the workload and soak tests reported `Intel(R) Xeon(R) CPU @ 2.20GHz`, and the N2 nodes reported `Intel(R) Xeon(R) CPU @ 2.80GHz`. We never got an AMD E2 node, so these results cover Intel E2 hosts only.

## Tests and pass criteria

We numbered each step B1 through B6, with "B" for bench. B1 created the pools, B2 through B5 are the tests below, and B6 deleted the pools. The raw result files use the same IDs.

We set every limit before running the tests. The main one: on any single-threaded task, E2 could take at most 1.30x as long as N2.

| ID | Test | Method | E2 passes if |
|---|---|---|---|
| B2 | Node ready | Seconds from a pending pod to node `Ready`, 3 runs per pool | Within 60 s of N2 |
| B3 | Cold start | Pull the 5 largest user images (23.9 GB) on a fresh node, 3 runs per pool | At most 1.25x N2 |
| B4 | Single thread | Python loop, numpy matmul (1 thread), pandas groupby and merge, R matmul, R loop. Median of 5 repetitions, 3 rounds | At most 1.30x N2 on each task |
| B4 | Multi-core | The Python loop in 8 processes at once | At least 0.75x N2 throughput |
| B4 | Notebooks | 5 test notebooks from our base user image, run end to end | At most 1.30x N2 |
| B5 | Soak | The 5 single-thread tasks once every 15 minutes for 24 hours | p95/p50 at most 1.20 |

## Results

E2 failed three checks:

| Test | E2 result | Failed on |
|---|---|---|
| Single thread, workload test | 1.16x to 1.70x N2 | numpy and R matmul |
| Single thread, soak medians | 1.63x to 2.58x N2 | All 5 tasks |
| Soak variance | p95/p50 from 1.09 to 1.39 (N2: 1.01 to 1.08) | pandas |

E2 passed four:

| Test | E2 result |
|---|---|
| Node ready | 12 s slower than N2 at the median |
| Cold start | 1.05x N2 at the median, 1.43x on the worst run |
| Multi-core | 0.81x N2 throughput |
| Notebooks | 1.15x N2 |

### Node ready

Median seconds from pod creation to node `Ready`: E2 54 s (runs: 54, 66, 50) and N2 42 s (42, 39, 52).

### Cold start

The test started one pod per image. That filled the 3 parallel pull slots the same way image-puller DaemonSets do on a new node. We measured from the first `Pulling` event to the last `Pulled` event.

| Run | E2 | N2 |
|---|---|---|
| 1 | 8m18s | 7m55s |
| 2 | 11m21s | 7m39s |
| 3 | 8m11s | 8m00s |
| Median | 8m18s | 7m55s |

N2's three runs stayed within 21 s of each other. E2 spread over 3m10s, and the slow run came from a different managed instance group than the other two.

Both families spent about 30% of node CPU time in iowait during the pulls, so the boot disk may limit cold start on both. We didn't measure disk throughput.

### Workloads

Median of 3 rounds, each the median of 5 repetitions. The notebooks are the test notebooks from our base user image: `all_hub_basic_notebook.ipynb`, `cpp_kernel_test.ipynb`, `gpt2_stack_test.ipynb`, `Hawthorne_test.ipynb` and `lec01_executed_1.ipynb`. The notebooks are available [here](https://github.com/cal-icor/base-user-image/tree/main/image-tests/notebooks).

All 5 succeeded in every round on both pools.

| Task | E2 | N2 | E2 / N2 | Result |
|---|---|---|---|---|
| Python loop | 11.69 s | 9.33 s | 1.25x | Pass |
| numpy matmul, 1 thread | 1.43 s | 0.87 s | 1.64x | Fail |
| pandas groupby and merge | 2.34 s | 2.01 s | 1.16x | Pass |
| R matmul | 0.46 s | 0.27 s | 1.70x | Fail |
| R loop | 17.28 s | 14.06 s | 1.23x | Pass |
| numpy matmul, 8 threads | 0.40 s | 0.26 s | 1.55x | No limit set |
| Python loop, 8 processes | 25.72 s | 20.82 s | 0.81x throughput | Pass |
| 5 notebooks, total | 51.9 s | 45.3 s | 1.15x | Pass |

### 24-hour soak

One node per pool ran each single-thread task once every 15 minutes, from Monday 15:08 to Tuesday 14:40 Pacific. We stopped after 95 of the planned 96 rounds, which gave 95 samples per task and pool. p95 uses the nearest-rank method.

| Task | E2 p50 | E2 p95 | E2 p95/p50 | N2 p50 | N2 p95 | N2 p95/p50 | E2 / N2 at p50 |
|---|---|---|---|---|---|---|---|
| Python loop | 12.23 s | 13.37 s | 1.09 | 6.13 s | 6.60 s | 1.08 | 1.99x |
| numpy matmul, 1 thread | 1.67 s | 1.88 s | 1.13 | 0.71 s | 0.74 s | 1.03 | 2.34x |
| pandas groupby and merge | 3.37 s | 4.70 s | 1.39 | 1.31 s | 1.33 s | 1.02 | 2.58x |
| R matmul | 0.54 s | 0.64 s | 1.18 | 0.24 s | 0.24 s | 1.01 | 2.26x |
| R loop | 17.83 s | 20.05 s | 1.12 | 10.96 s | 11.02 s | 1.01 | 1.63x |

E2 had 17 results slower than 1.2x its own median, and N2 had none. The worst E2 round came at 09:10 Pacific on Tuesday, when the R loop took 34.2 s against a 17.8 s median. Weekday daytime (07:00 to 19:00) widened E2's p95 on four of the five tasks, and N2's day and night numbers matched.

### Why the workload and soak gaps differ

The soak's E2/N2 gaps are wider than the workload test's on every task. Most of that comes from N2: the soak's N2 node ran 11% to 35% faster than the workload test's N2 node, although both reported the same CPU model. The soak's E2 node ran 3% to 44% slower than the workload test's E2 node. We didn't find the cause.

With one node per pool in each test, read each set of ratios as one sample of the gap. Both samples put E2 over the 1.30x limit on matrix math. The multi-core and notebook passes came from the workload test's nodes. If that N2 node ran slow, those passes overstate E2.

## Limitations

- Each test ran on one node per pool, and N2 nodes alone differed by up to 35% on the same task.
- Every E2 node we recorded was Intel. AMD E2 hosts may behave differently.
- One region (us-central1), and one Monday-to-Tuesday window for the soak.
- The tasks are synthetic. We haven't put real users on E2.

## What we're doing next

We'll add E2 as a lower priority in a GKE ComputeClass for user nodes. The autoscaler then creates E2 nodes when it can't create N2 nodes. During a stockout that hits every zone, the other choice is no node at all, and a slower node beats that. N2 stays our primary user pool. We haven't tested N4 or N2D.

## Questions for Google Cloud

1. When `n2-highmem-8` runs out in every us-central1 zone, how likely is `e2-highmem-8` capacity to be free?
2. Two N2 nodes with the same CPU model string differed by up to 35% on the same single-threaded task. Is that spread expected?

## Reproduce the tests

The raw result files are in the `results/` directory next to this report: `b2.txt`, `b3-<family>-<run>.txt`, `b4-<family>-<run>.jsonl` and `b5-<family>.jsonl`.

Replace `$CLUSTER`, `$REGION`, `$ZONES` and `$IMAGE` with your own values. `$IMAGE` needs Python 3 with numpy and pandas, R, and `jupyter nbconvert`. Add any flags your live user pool uses, such as private nodes, network tags or shielded VM settings.

1. Create the two test pools and the `bench` namespace (first box).
2. Load the script: `kubectl -n bench create configmap bench-script --from-file=bench.py`.
3. Run each test from the pod template (second box).
4. Save each test's output into `results/`.
5. Delete the pools and the namespace (end of the first box).

<details>
<summary>Create the two test pools (creates resources)</summary>

```bash
cat > bench-system-config.yaml <<'EOF'
kubeletConfig:
  cpuCFSQuota: false
  maxParallelImagePulls: 3
EOF

for mt in e2-highmem-8 n2-highmem-8; do
  fam=${mt%%-*}
  gcloud container node-pools create bench-$fam \
    --cluster $CLUSTER --region $REGION --node-locations $ZONES \
    --machine-type $mt \
    --disk-type pd-balanced --disk-size 200 --image-type COS_CONTAINERD \
    --max-pods-per-node 110 \
    --system-config-from-file bench-system-config.yaml \
    --node-labels bench=$fam \
    --node-taints bench=true:NoSchedule \
    --num-nodes 0 --enable-autoscaling --total-min-nodes 0 --total-max-nodes 1 \
    --location-policy ANY
done
kubectl create namespace bench
```

Remove everything afterward:

```bash
gcloud container node-pools delete bench-e2 --cluster $CLUSTER --region $REGION
gcloud container node-pools delete bench-n2 --cluster $CLUSTER --region $REGION
kubectl delete namespace bench
```

</details>

<details>
<summary>Pod template used by every test (creates a pod)</summary>

Each test applies this pod with its own name, image, command and requests. The node selector and toleration send it to one test pool.

```bash
fam=e2   # or n2
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: bench-soak-$fam
  namespace: bench
spec:
  nodeSelector:
    bench: $fam
  tolerations:
    - key: bench
      operator: Equal
      value: "true"
      effect: NoSchedule
  restartPolicy: Never
  terminationGracePeriodSeconds: 0
  containers:
    - name: soak
      image: $IMAGE
      command: ["/bin/sh", "-c", "python3 /bench/bench.py soak; sleep infinity"]
      resources:
        requests:
          cpu: "2"
          memory: 8Gi
      volumeMounts:
        - name: script
          mountPath: /bench
  volumes:
    - name: script
      configMap:
        name: bench-script
EOF
```

- Node ready: a `registry.k8s.io/pause:3.10` pod requesting 100m CPU. Record the pod's `creationTimestamp` and the node's `Ready` transition time.
- Cold start: one pod per image, each with `imagePullPolicy: Always` and the command `true`. Read the times from `kubectl get events` within an hour, before the cluster drops them.
- Workloads: a pod requesting 6 CPU and 24Gi with the command `sleep infinity`. Copy the notebooks in, then run `kubectl exec <pod> -- python3 /bench/bench.py full --notebooks '/tmp/bench-notebooks/*.ipynb'` 3 times.
- Soak: the manifest above. Collect with `kubectl -n bench logs bench-soak-$fam > b5-$fam.jsonl`.

</details>

<details>
<summary>bench.py (runs inside the pod, no cluster access)</summary>

```python
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
```

</details>

<details>
<summary>Analysis queries (read-only)</summary>

```bash
# Node CPU during cold start: Prometheus range query, 60 s step,
# from 2 minutes after the first Pulling event to the last Pulled event.
1 - avg(rate(node_cpu_seconds_total{node="<node>",mode="idle"}[3m]))
avg(rate(node_cpu_seconds_total{node="<node>",mode="iowait"}[3m]))

# List prices: Cloud Billing Catalog, Compute Engine service 6F81-5844-456A,
# SKUs "E2|N2 Instance Core|Ram running in Americas", OnDemand, us-central1.
# E2: 8 x 0.02181159 + 64 x 0.00292353 = 0.3616/h
# N2: 8 x 0.031611   + 64 x 0.004237   = 0.5241/h
```

</details>
