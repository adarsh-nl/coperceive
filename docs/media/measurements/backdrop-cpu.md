# Connect backdrop CPU

Measured by `scripts/desktop/measure_backdrop_cpu.sh` on the macOS runner while the Connect backdrop animated at 1280×800.

Source: [39923652622](https://github.com/adarsh-nl/coperceive/actions/runs/39923652622) at `d35dddd`. The public check annotations are the record. The runner log itself is not public.

- Motion path: `animated`. `COPERCEIVE_FORCE_MOTION=1` is honoured only when `COPERCEIVE_TEST_ENV` is also set, and it ignores `prefers-reduced-motion`.
- Warm-up dropped: first 5 s (not included below).
- Samples: 60 × 1 s. Each value is the sum of `ps` `%cpu` for the app process tree.
- Median: 0.6
- p95: 0.8 (nearest rank, index ceil(0.95 × n) − 1)
- n: 60
- Target: median under 5% of one core. Met.

The earlier run [39828222428](https://github.com/adarsh-nl/coperceive/actions/runs/39828222428) does not count. Its annotation said `reduceMotion=1`, so the backdrop drew one static frame.

## Samples (%cpu)

`0.6, 0.7, 0.5, 0.5, 0.6, 0.5, 0.8, 0.5, 0.7, 0.4, 0.4, 0.6, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 2.3, 0.8, 0.7, 0.6, 0.6, 0.5, 0.7, 0.5, 0.5, 0.6, 0.6, 0.5, 0.5, 0.5, 0.5, 0.5, 0.7, 0.5, 0.5, 0.5, 0.5, 0.6, 1.1, 0.7, 0.7, 0.6, 0.5, 0.7, 0.7, 0.6, 0.6, 0.5, 0.7, 1.0, 0.8, 0.5, 0.6, 0.6, 0.6, 0.7, 0.6`
