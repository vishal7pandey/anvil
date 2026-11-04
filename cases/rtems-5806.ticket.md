## Observed

`rtems_counter_ticks_to_sbintime()` returns wrong values once the interval exceeds a couple of
seconds, and `rtems_counter_ticks_to_nanoseconds()` is wrong past about 4.29 s: the reported
durations come out far too small, as if the interval had been truncated. At short intervals
(around 1 s and below) both conversions are correct.

The test framework measures elapsed time with `rtems_counter_ticks_to_sbintime()`, so long-running
performance tests report nonsense: a run that took ~731 s was reported as ~107 s.

## Steps to reproduce

```c
uint64_t ticks = /* counter ticks equivalent to ~10 s */;
sbintime_t  sb  = rtems_counter_ticks_to_sbintime( ticks );
uint64_t    ns  = rtems_counter_ticks_to_nanoseconds( ticks );
/* sb is not 10 s; ns is much smaller than 10e9 */
```

Also: convert the maximum number of counter ticks and compare with the expected duration.

## Expected

Conversions must be correct for any tick count, including intervals longer than a few seconds and
the maximum representable tick value.

## Environment

Reproduce on `sparc/erc32` (SIS). The check belongs in `testsuites/sptests/spcpucounter01`, which
currently only exercises a one-second interval. Code base: branch `tc1/5806-base` of github.com/vishal7pandey/rtems.

## Done when

Root cause identified, fix on a branch, `spcpucounter01` extended so the conversion fails before
and passes after the fix on the simulator, PR open with CI green.
