## Observed

After `pthread_mutex_init()` fails with `EINVAL` for a priority-ceiling mutex whose ceiling is out of range, the same mutex object is still accepted by later calls: `pthread_mutex_trylock()` and `pthread_mutex_destroy()` return `EBUSY` instead of `EINVAL`, and `pthread_mutex_lock()` ends in a fatal data abort inside the thread queue code. With a zero-filled object the lock instead succeeds and the caller ends up at priority 0.

## Steps to reproduce

```c
pthread_mutexattr_t attr;
pthread_mutex_t     mtx;

memset( &mtx, 0xa5, sizeof( mtx ) );
pthread_mutexattr_init( &attr );
pthread_mutexattr_setprotocol( &attr, PTHREAD_PRIO_PROTECT );
pthread_mutexattr_setprioceiling( &attr, sched_get_priority_max( SCHED_FIFO ) + 1 );

pthread_mutex_init( &mtx, &attr ); /* EINVAL, as expected */
pthread_mutex_trylock( &mtx );     /* expected EINVAL, got EBUSY */
pthread_mutex_destroy( &mtx );     /* expected EINVAL, got EBUSY */
```

## Expected

A failed `pthread_mutex_init()` leaves `*mutex` unmodified, and later calls reject the object with `EINVAL`.

## Environment

Uniprocessor, RTEMS_DEBUG off. Reproduce on `sparc/erc32` (SIS). Code base: branch `tc1/5776-base` of github.com/vishal7pandey/rtems.

## Done when

Root cause identified, fix on a branch, a regression test in the POSIX test suite passes on the simulator, PR open with CI green.
