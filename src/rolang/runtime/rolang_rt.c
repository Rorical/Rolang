/**
 * Rolang Runtime Library
 *
 * Single C translation unit linked against every compiled Rolang program.
 * Layout (by section, in order of appearance):
 *
 *   1.  Memory allocation         rt_alloc / rt_free
 *   2.  Panic helpers             rt_panic / rt_panic_index_out_of_bounds /
 *                                 rt_panic_divide_by_zero / rt_panic_msg
 *   3.  Typed-object ARC          rt_obj_alloc / rt_obj_retain / rt_obj_release
 *                                 + TypeDescriptor / FieldDescriptor tables
 *   4.  Cycle-detecting GC        rt_gc_collect, candidate tracking
 *   5.  Dictionaries              rt_dict_new / set / get / iterate / free
 *   6.  Async runtime             rt_task_spawn / rt_task_join / rt_task_yield /
 *                                 rt_scheduler_run + task queue
 *   8.  Debug helper              rt_print_i64
 *   9.  StringVal helpers         rt_str_len / concat / slice / find / trim /
 *                                 replace / to_i64 / to_f64 / split / lines
 *   10. Character classification  rt_char_is_digit / alpha / alnum / space
 *   11. Generic vector            rt_gvec_* (any T)
 *   12. File I/O                  rt_file_open / read / write / seek + path-string
 *                                 wrappers (_s suffix)
 *   13. Formatting                rt_format_int / i64 / f64 / bool / str (single
 *                                 placeholder) + rt_fmt_args (multi-arg)
 *   14. Process / OS interface    rt_args_count / get / rt_env_get / set /
 *                                 rt_process_system / rt_exit / rt_panic_msg /
 *                                 rt_stdin_read_*
 *   15. Path manipulation         rt_path_join / dirname / basename / extension /
 *                                 exists / is_dir / is_file / resolve / dir_list
 *   16. C entry point             int main(argc, argv) — wraps __rolang_user_main
 */

#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stddef.h>  /* offsetof */

/* Platform-specific includes for task yielding */
#if defined(__linux__) || defined(__APPLE__)
#include <unistd.h>
#include <sched.h>
#endif

/* Forward declarations for generic-vector helpers. Used by string-split,
 * directory-list, etc. before their definitions appear later in this file. */
void* rt_gvec_new(int32_t capacity, int32_t elem_size, int32_t elem_type_id);
void* rt_gvec_push(void* vec, const void* value);

/* ============================================================================
 * Object Pool Allocator — lock-free free-list for small ARC objects
 * ============================================================================
 *
 * Every rt_obj_alloc call for objects ≤ 256 total bytes (header + payload)
 * is served from a per-size-class free-list. "From pool" is exactly
 * (total ≤ POOL_MAX_TOTAL_SIZE): small objects are always pooled (rt_obj_alloc
 * never OS-allocates them), so the free path recovers both that fact and the
 * size class from the type descriptor — nothing is stored per-object.
 * On deallocation, pooled objects are pushed back onto their free-list
 * instead of being returned to the OS.
 */
#define POOL_BIN_COUNT      6
#define POOL_MAX_TOTAL_SIZE 256
/* ObjHeader is 32 bytes on 64-bit — defined later in this file */
#define _OBJ_HEADER_SIZE    32

/* Size classes are total bytes (32B header + payload). Profiled with
 * -DROLANG_POOL_PROFILE (see below): the benchmark suite barely exercises the
 * pool — fib/mandelbrot allocate zero typed objects, json_parse allocates 2,
 * and a 160k-allocation stress test lands 100% in the 48..64B classes. No
 * workload pressures the 96..256 bins or argues for different boundaries, so
 * the classes are left as-is; re-profile against a representative
 * allocation-heavy program before retuning. */
static const size_t pool_bin_sizes[POOL_BIN_COUNT] = {48, 64, 96, 128, 192, 256};

typedef struct PoolNode { struct PoolNode* __volatile next; } PoolNode;
/* NOT static: the codegen-emitted inline allocation fast path (see
 * _declare_inline_obj_alloc_fast in codegen/runtime.py) links directly
 * against these. Hidden visibility keeps them out of the dylib ABI. */
PoolNode* volatile pool_free_lists[POOL_BIN_COUNT];

static int pool_bin_for_size(size_t total_size) {
    for (int i = 0; i < POOL_BIN_COUNT; i++) {
        if (total_size <= pool_bin_sizes[i]) return i;
    }
    return -1;
}

static void* pool_try_alloc(int bin) {
    PoolNode* node;
#if defined(ROLANG_SINGLE_THREADED)
    /* No concurrency: a plain pop is correct and avoids the atomic RMW that
     * dominates alloc-churn workloads (e.g. binary_trees). */
    node = pool_free_lists[bin];
    if (node == NULL) return NULL;
    pool_free_lists[bin] = node->next;
    return (void*)node;
#else
    /* Lock-free pop from singly-linked free list */
    do {
        node = pool_free_lists[bin];
        if (node == NULL) return NULL;
    } while (!__sync_bool_compare_and_swap(&pool_free_lists[bin], node, node->next));
    return (void*)node;
#endif
}

static void pool_free_object(void* ptr, size_t total_size) {
    int bin = pool_bin_for_size(total_size);
    if (bin < 0) return;
    PoolNode* node = (PoolNode*)ptr;
#if defined(ROLANG_SINGLE_THREADED)
    /* No concurrency: a plain push is correct (see pool_try_alloc). */
    node->next = pool_free_lists[bin];
    pool_free_lists[bin] = node;
#else
    PoolNode* head;
    /* Lock-free push onto singly-linked free list */
    do {
        head = pool_free_lists[bin];
        node->next = head;
    } while (!__sync_bool_compare_and_swap(&pool_free_lists[bin], head, node));
#endif
}

static void* pool_obj_alloc(size_t payload_size, int64_t align) {
    size_t total = (size_t)_OBJ_HEADER_SIZE + payload_size;
    if (total > POOL_MAX_TOTAL_SIZE) return NULL;
    
    int bin = pool_bin_for_size(total);
    if (bin < 0) return NULL;
    
    /* Round up to the bin's actual allocation size */
    size_t alloc_size = pool_bin_sizes[bin];
    
    void* obj = pool_try_alloc(bin);
    if (obj == NULL) {
        /* Pool empty; allocate from OS. Use the bin size (rounded up)
         * so later deallocs always hit the same bin. */
        /* Round allocation size up to the bin size and to alignment */
        size_t aligned_size = (alloc_size + ((size_t)align - 1)) & ~((size_t)align - 1);
        obj = aligned_alloc((size_t)align, aligned_size);
        if (obj == NULL) return NULL;
    }
    return obj;
}
/* ============================================================================*/

#ifdef ROLANG_POOL_PROFILE
/* ----------------------------------------------------------------------------
 * Allocation-size profiler. Build the runtime with -DROLANG_POOL_PROFILE to
 * record the total size (32-byte header + payload) of every typed-object
 * allocation into 16-byte buckets and dump a histogram at exit — the data that
 * drives the pool size-class table above. Compiled out of normal builds, so
 * leaving it in place is free. Single-threaded runtime → plain counters. */
#include <stdio.h>
#include <stdlib.h>
#define POOL_PROFILE_NBUCKETS 64        /* up to 1024 B in 16-byte steps */
static long rt_pool_profile_hist[POOL_PROFILE_NBUCKETS];
static long rt_pool_profile_over;
static int  rt_pool_profile_registered;
static void rt_pool_profile_dump(void) {
    long total = 0;
    for (int i = 0; i < POOL_PROFILE_NBUCKETS; i++) total += rt_pool_profile_hist[i];
    total += rt_pool_profile_over;
    if (total == 0) return;
    fprintf(stderr, "=== ROLANG POOL PROFILE (typed-object total = 32B header + payload) ===\n");
    long cum = 0;
    for (int i = 0; i < POOL_PROFILE_NBUCKETS; i++) {
        long c = rt_pool_profile_hist[i];
        if (c == 0) continue;
        cum += c;
        fprintf(stderr, "  %4d..%4d B : %10ld  (%5.1f%%, cum %5.1f%%)\n",
                i * 16, i * 16 + 15, c, 100.0 * c / total, 100.0 * cum / total);
    }
    if (rt_pool_profile_over)
        fprintf(stderr, "  > pool max   : %10ld  (%5.1f%%) [OS malloc path]\n",
                rt_pool_profile_over, 100.0 * rt_pool_profile_over / total);
    fprintf(stderr, "  total typed allocations: %ld\n", total);
}
static void rt_pool_profile_record(size_t total) {
    if (!rt_pool_profile_registered) {
        rt_pool_profile_registered = 1;
        atexit(rt_pool_profile_dump);
    }
    size_t b = total / 16;
    if (b < POOL_PROFILE_NBUCKETS) rt_pool_profile_hist[b]++;
    else rt_pool_profile_over++;
}
#endif /* ROLANG_POOL_PROFILE */

/* ============================================================================
 * Memory Management (raw allocation — unchanged)
 * ============================================================================ */

/**
 * Allocate memory with specified size and alignment.
 *
 * Used for internal allocations (frames, buffers, string data).
 * For typed heap objects, use rt_obj_alloc instead.
 *
 * @param size  Number of bytes to allocate
 * @param align Alignment requirement (power of 2)
 * @return Pointer to allocated memory, or NULL on failure
 */
void* rt_alloc(int64_t size, int64_t align) {
    if (size <= 0) {
        return NULL;
    }

    // aligned_alloc is C11 and present on every supported platform (glibc,
    // musl, macOS 10.15+). Using it unconditionally keeps rt_free a plain
    // free() everywhere, so pointers from rt_alloc AND from plain malloc
    // (e.g. runtime StringVal handles) can both be released through rt_free.
    // The old _POSIX_C_SOURCE-guarded fallback stashed the raw pointer at
    // ptr[-1]; on platforms that took it (macOS), rt_free then read garbage
    // when handed a plain-malloc'd pointer and aborted in libmalloc.
    // Ensure alignment is at least sizeof(void*) and size is a multiple of
    // alignment (C11 requires it). Cast sizeof(void*) to int64_t since
    // `align` is signed and an unsigned comparison would treat negative
    // values as huge positives.
    if (align < (int64_t)sizeof(void*)) {
        align = (int64_t)sizeof(void*);
    }
    // Round up size to be a multiple of alignment
    int64_t aligned_size = (size + align - 1) & ~(align - 1);
    return aligned_alloc((size_t)align, (size_t)aligned_size);
}

/**
 * Free previously allocated memory.
 *
 * @param ptr Pointer returned by rt_alloc (or NULL)
 */
void rt_free(void* ptr) {
    if (ptr == NULL) {
        return;
    }

    free(ptr);
}

/* ============================================================================
 * Panic / abort path
 *
 * Used by codegen-emitted runtime checks (divide-by-zero, array out-of-bounds,
 * etc.) and by direct rt_* helpers below. Prints a diagnostic on stderr in a
 * "rolang panic: ..." format and aborts the process. Never returns.
 *
 * The "ctx" string is a short description of the failing operation, e.g.
 * "array index out of bounds" or "integer divide by zero". "extra" gives an
 * operation-dependent numeric detail (index value, modulus value, ...).
 *
 * For ergonomics, the wrappers below all have predictable names that codegen
 * can rely on (rt_panic_index_out_of_bounds, rt_panic_divide_by_zero).
 * ============================================================================ */

__attribute__((noreturn))
void rt_panic(const char* ctx) {
    if (ctx == NULL) ctx = "(unknown)";
    fprintf(stderr, "rolang panic: %s\n", ctx);
    fflush(stderr);
    abort();
}

__attribute__((noreturn))
void rt_panic_index_out_of_bounds(int64_t index, int64_t len) {
    fprintf(stderr,
            "rolang panic: index out of bounds: the len is %lld but the index is %lld\n",
            (long long)len, (long long)index);
    fflush(stderr);
    abort();
}

__attribute__((noreturn))
void rt_panic_divide_by_zero(void) {
    fprintf(stderr, "rolang panic: attempt to divide by zero\n");
    fflush(stderr);
    abort();
}

__attribute__((noreturn))
void rt_panic_remainder_by_zero(void) {
    fprintf(stderr, "rolang panic: attempt to calculate the remainder with a divisor of zero\n");
    fflush(stderr);
    abort();
}

/*
 * Emitted by codegen for `expr as! TargetType` when the dynamic witness
 * pointer doesn't match the expected (target, protocol) pair. The cast
 * cannot proceed safely — abort with a clear diagnostic.
 */
__attribute__((noreturn))
void rt_panic_invalid_cast(void) {
    fprintf(stderr,
            "rolang panic: forced downcast (`as!`) failed: existential does "
            "not carry the expected concrete type\n");
    fflush(stderr);
    abort();
}

/* ============================================================================
 * Typed-Object System
 *
 * Every heap-allocated struct/enum/tuple has this layout:
 *
 *   Offset 0:  int64_t          rc           Reference count
 *   Offset 8:  uint64_t         type_id      Index into descriptor table
 *   Offset 16: struct ObjHeader* prev         GC list back-link (doubly-linked)
 *   Offset 24: struct ObjHeader* next         GC list forward-link
 *   Offset 32: <payload>                     Actual struct/enum/tuple data
 *
 * The payload starts at OBJ_HEADER_SIZE (32 bytes on 64-bit).
 * ============================================================================ */

#include <stdatomic.h>

typedef struct ObjHeader {
    int64_t           rc;          /* refcount. Non-atomic under
                                    * ROLANG_SINGLE_THREADED (default); undefine
                                    * that flag to restore atomics if a
                                    * multi-threaded runtime is ever added. */
    uint64_t          type_id;
    struct ObjHeader* prev;        /* gc_object_list back-link (NULL at head).
                                    * Reuses the reserved _pad slot so
                                    * gc_list_remove is O(1) instead of an O(n)
                                    * scan. Pool metadata is recovered at free
                                    * from the type descriptor. */
    struct ObjHeader* next;        /* gc_object_list forward-link */
} ObjHeader;

_Static_assert(offsetof(ObjHeader, rc) == 0,
    "inline retain/release IR assumes rc is the first header field (offset 0)");

#define OBJ_HEADER_SIZE  ((int64_t)sizeof(ObjHeader))
#define OBJ_HEADER(ptr)  ((ObjHeader*)(ptr))
#define OBJ_PAYLOAD(ptr) ((void*)((char*)(ptr) + OBJ_HEADER_SIZE))

/* The pool sizes objects before ObjHeader is defined, so it carries its own
 * header-size literal. Tie it to the real struct so the two can never drift —
 * and so resizing the header is caught here instead of corrupting the heap.
 * NOTE: codegen bakes the SAME payload offset via OBJ_HEADER_SIZE in
 * codegen/types.py; that constant must be changed in lockstep (no cross-
 * language assert can guard it). */
_Static_assert(_OBJ_HEADER_SIZE == sizeof(ObjHeader),
    "_OBJ_HEADER_SIZE (pool) must equal sizeof(ObjHeader); also update "
    "OBJ_HEADER_SIZE in codegen/types.py to match the payload offset");

/* ============================================================================
 * Type Descriptors
 *
 * Each struct/enum type gets a static TypeDescriptor emitted by the codegen.
 * The GC uses these to find pointer fields within objects.
 * ============================================================================ */

typedef struct {
    int32_t  offset;        /* Byte offset of this field within the payload */
    uint64_t field_type_id; /* Descriptor id for the pointed-to field type */
    int32_t  case_tag;      /* -1 = struct/tuple field, >=0 = enum case tag */
    int32_t  _pad;
} FieldDescriptor;

/*
 * Pointer to a generated `void deinit(void* payload)` C ABI function or NULL.
 * If non-NULL, rt_obj_release calls it on the final reference-count
 * decrement, BEFORE releasing the object's pointer fields. This lets user
 * `deinit { ... }` blocks observe the object in a still-valid state.
 *
 * MUST stay in sync with the LLVM struct layout emitted by codegen in
 * _emit_type_descriptor_table.
 */
typedef void (*DeinitFn)(void* payload);

/*
 * Optional cycle-collector trace hook. ``trace_fn`` is called by the GC
 * for any type whose managed pointers are not described by the static
 * field-descriptor list — most notably ``Vec<T>`` and ``Dict<K, V>``,
 * whose heap-typed slots live inside a separately-allocated buffer
 * reached through a ``RawPtr`` field that the descriptor table cannot
 * see into. The trace function receives the object's payload pointer
 * and a callback to invoke for every heap-typed managed pointer it can
 * find. ``ctx`` is opaque to the trace function and forwarded verbatim.
 */
typedef void (*GCTraceCb)(void* target, void* ctx);
typedef void (*GCTraceFn)(void* payload, GCTraceCb cb, void* ctx);

/*
 * Pointer to a codegen-generated `void release_fields(void* payload)` that
 * ARC-releases this type's heap pointer fields directly, with constant
 * offsets baked in — a specialized, branch-predictable replacement for the
 * generic obj_release_fields descriptor walk on the hot teardown path. NULL
 * for types with no heap fields (and for the weak default table); the runtime
 * then falls back to obj_release_fields, which no-ops when field_count == 0.
 * Generated from the SAME field-descriptor data, so it is exactly equivalent.
 */
typedef void (*ReleaseFieldsFn)(void* payload);

typedef struct {
    uint64_t        type_id;       /* Unique ID for this type */
    int64_t         payload_size;  /* Size of the data after the header */
    int32_t         field_count;   /* Number of pointer fields */
    int32_t         fields_start;  /* Start index in RT_TYPE_FIELD_DESCRIPTORS */
    DeinitFn        deinit_fn;     /* User deinit hook, or NULL */
    GCTraceFn       trace_fn;      /* Container GC trace hook, or NULL */
    int32_t         acyclic;       /* 1 = instances can never be in a cycle */
    ReleaseFieldsFn release_fields_fn; /* Per-type field-release fast path, or NULL.
                                        * Append-only: existing field offsets
                                        * (deinit_fn@24, trace_fn@32, acyclic@40)
                                        * are unchanged. */
} TypeDescriptor;

/* The LLVM desc_type in codegen/__init__.py emits these fields in this exact
 * order, with `acyclic` appended LAST. Guard the one field this change added:
 * if anyone inserts a field between trace_fn and acyclic, the runtime would
 * read acyclic at an offset codegen never wrote. Relational (not a hardcoded
 * offset) so it holds on any pointer width as long as the order is preserved. */
_Static_assert(offsetof(TypeDescriptor, acyclic)
                   == offsetof(TypeDescriptor, trace_fn) + sizeof(GCTraceFn),
    "TypeDescriptor.acyclic must immediately follow trace_fn to stay in sync "
    "with the LLVM desc_type emission in codegen/__init__.py");

/*
 * Global type descriptor table — flat array of TypeDescriptor structs.
 * Weak default; codegen provides the real table with strong definitions.
 */
__attribute__((weak))
TypeDescriptor RT_TYPE_DESCRIPTORS[1] = {{0, 0, 0, 0, NULL, NULL, 0, NULL}};
__attribute__((weak))
int32_t RT_TYPE_DESCRIPTOR_COUNT = 0;

/*
 * Flat array of field descriptors. RT_TYPE_DESCRIPTORS[i].fields_start
 * gives the starting index into this array; field_count gives the count.
 */
__attribute__((weak))
FieldDescriptor RT_TYPE_FIELD_DESCRIPTORS[1] = {{0, 0, -1, 0}};
__attribute__((weak))
int32_t RT_TYPE_FIELD_DESCRIPTOR_COUNT = 0;

/* ============================================================================
 * Global Object Registry (for GC traversal)
 *
 * Singly-linked list of all live typed objects.  Protected by a spinlock.
 * ============================================================================ */

ObjHeader* gc_object_list = NULL;  /* non-static: see inline alloc fast path */
static atomic_flag gc_list_lock = ATOMIC_FLAG_INIT;

/* Generational boundary into gc_object_list (youngest at head):
 *   [gc_object_list .. gc_old_head)  = YOUNG (allocated since the last collect)
 *   [gc_old_head    .. NULL]         = OLD   (survived >=1 collection, tenured)
 * A *minor* collection scans only the young region; a *major* (every
 * GC_MAJOR_EVERY minors, and the very first collection) scans everything. This
 * keeps a large persistent cyclic-capable heap from being re-scanned on every
 * pass. Encoding the generation as list position avoids needing a per-object
 * field (the 32-byte ObjHeader is full). NULL means "all objects are young"
 * (forces a major). gc_list_remove maintains this boundary in O(1). */
static ObjHeader* gc_old_head = NULL;
static int        gc_minor_count = 0;
#define GC_MAJOR_EVERY 8

int64_t gc_alloc_counter = 0;          /* non-static: inline alloc fast path */
int64_t gc_last_collect_count = 0;     /* non-static: inline alloc fast path */
static int64_t gc_cycle_count = 0;

/* Adaptive cycle-GC threshold. Instead of a fixed gap between collections,
 * scale the gap with the live set that survives each pass: a program with a
 * large persistent heap then amortizes each O(live) cycle scan over a
 * proportional number of subsequent allocations instead of rescanning every
 * GC_MIN_GAP allocations. Bounded by a floor and a cap. */
#define GC_MIN_GAP   10000
#define GC_MAX_GAP   2000000
#define GC_GROWTH    2
int64_t gc_next_gap = GC_MIN_GAP;      /* non-static: inline alloc fast path */
/* Precomputed trigger threshold: gc_last_collect_count + gc_next_gap. The
 * per-allocation poll is then one load + one compare against the counter.
 * Every site that updates the clock or the gap must refresh it. */
int64_t gc_trigger_at = GC_MIN_GAP;    /* non-static: inline alloc fast path */

/* ---- GC list lock helpers ---- */

static inline TypeDescriptor* rt_get_type_descriptor(uint64_t type_id) {
    if (type_id >= (uint64_t)RT_TYPE_DESCRIPTOR_COUNT) {
        return NULL;
    }
    return &RT_TYPE_DESCRIPTORS[type_id];
}

static inline int32_t rt_get_field_count(const TypeDescriptor* desc) {
    if (desc == NULL) return 0;
    return desc->field_count;
}

static inline FieldDescriptor* rt_get_field_descriptors(const TypeDescriptor* desc) {
    if (desc == NULL || desc->field_count == 0) return NULL;
    if (desc->fields_start < 0) return NULL;
    if (desc->fields_start + desc->field_count > RT_TYPE_FIELD_DESCRIPTOR_COUNT) return NULL;
    return &RT_TYPE_FIELD_DESCRIPTORS[desc->fields_start];
}

volatile int gc_running = 0;  /* Set to 1 during rt_gc_collect; non-static:
                               * inline alloc fast path reads it */

/* Defined below. rt_obj_alloc polls it once the alloc counter crosses the gap,
 * so the GC trigger lives at the one site allocations happen rather than being
 * polled before every statement by codegen. */
void rt_gc_collect(void);

static void gc_list_lock_acquire(void) {
#ifndef ROLANG_SINGLE_THREADED
    /* If GC is already running (called from within obj_release_fields
     * during step 5), don't re-acquire the lock — the GC holds it. */
    if (gc_running) return;
    while (atomic_flag_test_and_set_explicit(&gc_list_lock, memory_order_acquire)) {
        /* spin */
    }
#endif
    /* Single-threaded cooperative runtime: the GC list has no concurrent
     * accessors, so the per-allocation/-free lock is pure overhead. */
}

static void gc_list_lock_release(void) {
#ifndef ROLANG_SINGLE_THREADED
    if (gc_running) return;
    atomic_flag_clear_explicit(&gc_list_lock, memory_order_release);
#endif
}

/* ---- GC list operations ---- */

static void gc_list_add(ObjHeader* obj) {
    if (!gc_running) gc_list_lock_acquire();
    obj->prev = NULL;
    obj->next = gc_object_list;
    if (gc_object_list != NULL) gc_object_list->prev = obj;
    gc_object_list = obj;
    gc_alloc_counter++;
    if (!gc_running) gc_list_lock_release();
}

/* O(1) unlink from the doubly-linked gc_object_list. */
static void gc_list_remove(ObjHeader* obj) {
    if (!gc_running) gc_list_lock_acquire();
    ObjHeader* p = obj->prev;
    ObjHeader* n = obj->next;
    /* Keep the generational boundary valid if we unlink the first old object.
     * (During a collection gc_old_head is NULL, so this is a no-op then.) */
    if (obj == gc_old_head) gc_old_head = n;
    if (p != NULL) p->next = n;
    else           gc_object_list = n;   /* obj was the head */
    if (n != NULL) n->prev = p;
    obj->prev = NULL;
    obj->next = NULL;
    if (!gc_running) gc_list_lock_release();
}

/* ============================================================================
 * Typed-Object Allocation
 * ============================================================================ */

/**
 * Allocate a typed heap object.
 *
 * Allocates header + payload, initializes rc=1, sets type_id, links into
 * the GC registry.
 *
 * @param payload_size Size of the data payload in bytes
 * @param align        Alignment requirement (power of 2)
 * @param type_id      Index into RT_TYPE_DESCRIPTORS table
 * @return Pointer to the ObjHeader (start of the object), or NULL on failure
 */
static inline void* _obj_alloc_impl(int64_t payload_size, int64_t align,
                                    uint64_t type_id, int zero_payload) {
    int64_t total_size = OBJ_HEADER_SIZE + payload_size;

#ifdef ROLANG_POOL_PROFILE
    rt_pool_profile_record((size_t)total_size);
#endif

#ifdef ROLANG_CHECK_PAYLOAD
    {
        TypeDescriptor* _d = rt_get_type_descriptor(type_id);
        if (_d != NULL && _d->payload_size != payload_size) {
            fprintf(stderr, "ROLANG_CHECK_PAYLOAD type_id=%llu param=%lld desc=%lld\n",
                    (unsigned long long)type_id, (long long)payload_size,
                    (long long)_d->payload_size);
        }
    }
#endif

    /* Small objects ALWAYS come from the per-size-class pool; large ones from
     * the OS. Small objects do not fall back to rt_alloc on pool OOM (which
     * would yield a differently-sized block) — that keeps "from pool" exactly
     * equal to (total <= POOL_MAX_TOTAL_SIZE), so the free path can recover it
     * (and the pool bin) from the type descriptor without storing it here. */
    void* raw;
    if ((size_t)total_size <= POOL_MAX_TOTAL_SIZE) {
        raw = pool_obj_alloc((size_t)payload_size, align);
    } else {
        raw = rt_alloc(total_size, align);
    }
    if (raw == NULL) return NULL;

    ObjHeader* h = (ObjHeader*)raw;
    h->rc = 1;
    h->type_id = type_id;
    if (zero_payload && payload_size > 0) {
        if (payload_size <= 64) {
            /* Inline word stores instead of a memset libcall. Both the pool
             * (bin sizes) and rt_alloc (size rounded up to align) guarantee
             * the allocation extends to an 8-byte boundary past the payload,
             * so rounding the zero-fill up to a multiple of 8 stays in
             * bounds. */
            int64_t* p = (int64_t*)OBJ_PAYLOAD(h);
            int64_t words = (payload_size + 7) >> 3;
            for (int64_t i = 0; i < words; i++) p[i] = 0;
        } else {
            memset(OBJ_PAYLOAD(h), 0, (size_t)payload_size);
        }
    }

    gc_list_add(h);   /* sets h->prev and h->next */

    /* Trigger cycle-GC from the one place the allocation counter advances,
     * instead of polling before every statement in codegen. A reference cycle
     * can only be created by allocating a new heap object, so this is the only
     * point a check could ever fire — which lets non-allocating hot loops run
     * with zero GC overhead.
     *
     * The new object `h` is at this point the GC-list HEAD and, for a noinit
     * allocation, its payload still holds stale pool bytes — the caller's
     * field stores run only after we return. rt_gc_collect therefore SKIPS
     * the list head (see Step 1 there): the collector must never interpret
     * those stale bytes as pointer fields. Every OTHER listed object is
     * fully constructed (field stores are straight-line code right after
     * their allocation; there is no allocation, release, or suspension
     * point in between). The threshold test is inlined so the common case
     * stays a load+compare; skip entirely while a collection is already
     * running (a deinit-triggered allocation). */
    if (!gc_running && gc_alloc_counter >= gc_trigger_at) {
        rt_gc_collect();
    }

    return raw;
}

void* rt_obj_alloc(int64_t payload_size, int64_t align, uint64_t type_id) {
    return _obj_alloc_impl(payload_size, align, type_id, /*zero_payload=*/1);
}

/* Allocation WITHOUT the payload zero-fill, for construction sites that
 * provably store every live field before the next allocation, release, or
 * GC-observable point: MakeStruct (all fields required by the language) and
 * MakeEnum (tag + the active case's payload; every descriptor walk —
 * release_fields, GC trace, clone — is tag-filtered, so the inactive union
 * bytes are never read). The zero-fill is a meaningful fraction of
 * alloc-churn workloads (binary_trees); for fully-stored payloads it is
 * pure waste. Callers that leave any live field unwritten MUST use
 * rt_obj_alloc instead. */
void* rt_obj_alloc_noinit(int64_t payload_size, int64_t align, uint64_t type_id) {
    return _obj_alloc_impl(payload_size, align, type_id, /*zero_payload=*/0);
}

/* ============================================================================
 * Typed-Object Reference Counting
 * ============================================================================ */

/* Forward declaration — used by obj_release_fields for recursive field cleanup */
void rt_obj_release(void* ptr);

/* Cold path: rc has just reached 0. Runs deinit, handles resurrection,
 * releases pointer fields, unlinks from the GC list, and frees. */
void rt_obj_release_slow(void* ptr);

/**
 * Increment reference count of a typed heap object.
 */
void rt_obj_retain(void* ptr) {
    if (ptr == NULL) {
        return;
    }
#ifdef ROLANG_FREE_STATS
    { extern void rt_retain_stat(void); rt_retain_stat(); }
#endif

    int64_t* refcount = &OBJ_HEADER(ptr)->rc;

#if defined(ROLANG_SINGLE_THREADED)
    (*refcount)++;   /* single-threaded runtime: atomics are pure overhead */
#elif defined(__GNUC__) || defined(__clang__)
    __atomic_fetch_add(refcount, 1, __ATOMIC_RELAXED);
#else
    (*refcount)++;
#endif
}

/**
 * Release pointer fields of a typed object (called before freeing).
 */
static void obj_release_fields(ObjHeader* h) {
    TypeDescriptor* desc = rt_get_type_descriptor(h->type_id);
    if (desc == NULL) {
        return;
    }

    int32_t field_count = rt_get_field_count(desc);
    if (field_count <= 0) {
        return;
    }

    FieldDescriptor* fields = rt_get_field_descriptors(desc);
    if (fields == NULL) {
        return;
    }

    void* payload = OBJ_PAYLOAD(h);
    /* For enum objects, read the tag once so we can filter case-specific fields */
    int32_t enum_tag = 0;
    int32_t tag_available = 0;

    for (int32_t i = 0; i < field_count; i++) {
        FieldDescriptor* fd = &fields[i];
        /* For enum payload fields, check the tag matches the active case */
        if (fd->case_tag >= 0) {
            if (!tag_available) {
                enum_tag = *(int32_t*)payload;
                tag_available = 1;
            }
            if (enum_tag != fd->case_tag) {
                continue;  /* This field belongs to a different enum case */
            }
        }
        void** field_ptr = (void**)((char*)payload + fd->offset);
        if (*field_ptr != NULL) {
            rt_obj_release(*field_ptr);
        }
    }
}

/**
 * Decrement reference count of a typed heap object.
 * If rc reaches 0, IMMEDIATELY release all pointer fields and free the object.
 *
 * This is the PRIMARY deallocation path. The GC is never involved unless a
 * reference cycle prevents rc from ever reaching 0.
 */
void rt_obj_release(void* ptr) {
    if (ptr == NULL) {
        return;
    }
#ifdef ROLANG_FREE_STATS
    { extern void rt_release_stat(void); rt_release_stat(); }
#endif

    ObjHeader* h = OBJ_HEADER(ptr);
    int64_t prev;

#if defined(ROLANG_SINGLE_THREADED)
    prev = (h->rc)--;
#elif defined(__GNUC__) || defined(__clang__)
    prev = __atomic_fetch_sub(&h->rc, 1, __ATOMIC_ACQ_REL);
#else
    prev = (h->rc)--;
#endif

    if (prev == 1) {
        rt_obj_release_slow(ptr);
    }
}

/**
 * Cold teardown path: the caller has already decremented rc to 0.
 * Split out of rt_obj_release so codegen can inline only the hot
 * null-check + decrement + compare and call here only when rc hits 0.
 * Performs NO decrement of its own.
 */
#ifdef ROLANG_FREE_STATS
#include <stdio.h>
#include <stdlib.h>
static long rt_free_count, rt_free_rc_nonzero, rt_retain_n, rt_release_n;
static int  rt_free_reg;
static void rt_free_dump(void) {
    fprintf(stderr, "=== FREE STATS === retains=%ld releases=%ld slow_frees=%ld (rc!=0: %ld)\n",
            rt_retain_n, rt_release_n, rt_free_count, rt_free_rc_nonzero);
}
static void rt_free_reg_once(void) { if (!rt_free_reg) { rt_free_reg = 1; atexit(rt_free_dump); } }
void rt_free_stat(int64_t rc_now) {
    rt_free_reg_once();
    rt_free_count++;
    if (rc_now != 0) rt_free_rc_nonzero++;
}
void rt_retain_stat(void) { rt_free_reg_once(); rt_retain_n++; }
void rt_release_stat(void) { rt_free_reg_once(); rt_release_n++; }
#endif

void rt_obj_release_slow(void* ptr) {
    ObjHeader* h = OBJ_HEADER(ptr);

#ifdef ROLANG_FREE_STATS
    {
        extern void rt_free_stat(int64_t rc_now);
        rt_free_stat(h->rc);
    }
#endif

    /* rc reached 0 — immediate free, no GC delay.
     *
     * Order matters:
     *   1. Run the user-declared `deinit` (if any) while the object is
     *      still fully valid — fields not yet released, pointer still
     *      live. This lets the deinit body call methods on `self`,
     *      access fields, etc.
     *   2. Detect deinit-resurrection: if the body stashed `self` in a
     *      reachable location (global, container) and retained, the
     *      object must NOT be freed — mirrors the cycle-GC behaviour
     *      in Phase 5c.
     *   3. Release pointer fields recursively (drops their refcounts).
     *   4. Unlink from GC list and free the allocation.
     */
    TypeDescriptor* desc = rt_get_type_descriptor(h->type_id);
    if (desc != NULL && desc->deinit_fn != NULL) {
        /* Pin rc to a sentinel value so any retain/release inside the
         * deinit observes a large positive count and cannot re-enter
         * the ``prev == 1`` branch via a balanced retain+release pair. */
        const int64_t PIN = (int64_t)0x4000000000000000LL;
        __atomic_store_n(&h->rc, PIN, __ATOMIC_RELAXED);
        desc->deinit_fn(ptr);
        int64_t cur = __atomic_load_n(&h->rc, __ATOMIC_RELAXED);
        int64_t extra = cur - PIN;
        if (extra > 0) {
            /* Resurrected — leave the object alive with the new rc and
             * skip teardown. The GC cycle path uses the same trick. */
            __atomic_store_n(&h->rc, extra, __ATOMIC_RELAXED);
            return;
        }
        /* Restore rc to 0 before tearing down so any debug introspection
         * sees the expected state. */
        __atomic_store_n(&h->rc, 0, __ATOMIC_RELAXED);
    }
    /* Release all pointer fields recursively. Prefer the codegen-generated
     * per-type fast path (constant offsets, no descriptor walk); fall back to
     * the generic walk for the weak default table / any type without one. */
    if (desc != NULL && desc->release_fields_fn != NULL) {
        desc->release_fields_fn(OBJ_PAYLOAD(h));
    } else {
        obj_release_fields(h);
    }
    gc_list_remove(h);       /* Remove from GC registry (O(1)) */
    /* Recover the pool bin from the type descriptor instead of a stored _pad:
     * total = header + payload, and rt_obj_alloc guarantees small objects
     * (total <= POOL_MAX_TOTAL_SIZE) are always pooled. If the descriptor is
     * missing (should not happen for a live object), free directly — pooled
     * blocks are aligned_alloc'd, so free() is valid. */
    int64_t total = (desc != NULL) ? (OBJ_HEADER_SIZE + desc->payload_size) : 0;
    if (desc != NULL && total <= (int64_t)POOL_MAX_TOTAL_SIZE) {
        pool_free_object(ptr, (size_t)total);
    } else {
        rt_free(ptr);
    }
}

/* ============================================================================
 * Object Cloning
 * ============================================================================ */

/**
 * Deep-copy a typed heap object.
 *
 * Allocates a new object with the same type descriptor, copies the payload
 * byte-for-byte, and retains all pointer fields.
 */
void* rt_obj_clone(void* ptr) {
    if (ptr == NULL) {
        return NULL;
    }

    ObjHeader* src = OBJ_HEADER(ptr);

    TypeDescriptor* desc = rt_get_type_descriptor(src->type_id);
    if (desc == NULL) {
        return NULL;
    }

    /* Allocate clone with same alignment as source.
     * Compute alignment from payload_size: round up to next power of 2,
     * capped at 16.  For vector/SIMD types the type descriptor can be
     * extended with an alignment field later. */
    int64_t align = 8;
    if (desc->payload_size > 8) align = 16;
    void* clone = rt_obj_alloc(desc->payload_size, align, src->type_id);
    if (clone == NULL) {
        return NULL;
    }

    /* Copy payload */
    memcpy(OBJ_PAYLOAD(clone), OBJ_PAYLOAD(ptr), (size_t)desc->payload_size);

    /* Retain all pointer fields in the clone */
    int32_t field_count = rt_get_field_count(desc);
    FieldDescriptor* fields = rt_get_field_descriptors(desc);
    if (field_count > 0 && fields != NULL) {
        void* clone_payload = OBJ_PAYLOAD(clone);
        int32_t enum_tag = 0;
        int32_t tag_available = 0;

        for (int32_t i = 0; i < field_count; i++) {
            FieldDescriptor* fd = &fields[i];
            if (fd->case_tag >= 0) {
                if (!tag_available) {
                    enum_tag = *(int32_t*)clone_payload;
                    tag_available = 1;
                }
                if (enum_tag != fd->case_tag) {
                    continue;
                }
            }
            void** field_ptr = (void**)((char*)clone_payload + fd->offset);
            if (*field_ptr != NULL) {
                rt_obj_retain(*field_ptr);
            }
        }
    }

    return clone;
}

/* ============================================================================
 * Cycle-Detecting GC
 *
 * The GC exists solely to detect and collect reference cycles. All non-cyclic
 * objects are already freed immediately by rt_obj_release when their rc hits 0.
 *
 * Algorithm: Synchronous trial deletion (CPython gc-module style)
 *
 *   1. Build candidate set from all live typed objects (skip rc==0)
 *   2. Subtract internal references (temporarily decrement rc for pointers
 *      between candidates)
 *   3. Objects with rc==0 are unreachable cycle members → collect
 *   4. Restore refcounts for survivors
 *   5. Free collected objects via rt_obj_release
 *
 * Thread safety: Stop-the-world (acquires gc_list_lock for duration).
 * ============================================================================ */

/* Temporary tracking per object during GC.
 *
 * The candidate buffer (and accompanying open-addressing hash table) grows
 * dynamically. */

typedef struct {
    ObjHeader* obj;
    int64_t   saved_rc;       /* Original rc before trial deletion */
    int32_t   collected;      /* 1 if marked for collection */
    int32_t   _pad;
} GCCandidate;

/* During a collection, candidate membership and reachability are tracked as
 * high bits stashed directly in each candidate's rc field, replacing the
 * previous pointer->index hash table: a membership test becomes one load+mask
 * on memory the collector touches anyway (no hashing, no probe chain, and no
 * table to memset every pass). The low 61 bits remain the trial refcount —
 * real refcounts can never approach 2^61, so the bits are unambiguous.
 * Survivors get their exact rc restored from saved_rc (clearing both bits);
 * collected objects carry their bits until pinned (deinit path) or freed. */
#define GC_CAND_BIT      ((int64_t)1 << 62)
#define GC_REACH_BIT     ((int64_t)1 << 61)
#define GC_RC_VALUE_MASK (GC_REACH_BIT - 1)

#define GC_INITIAL_CAPACITY 4096

static GCCandidate* gc_candidates = NULL;
static int32_t gc_candidates_capacity = 0;
static int32_t gc_candidate_count = 0;
static ObjHeader** gc_worklist = NULL;
static int32_t gc_worklist_capacity = 0;

/* Survivor count at the end of the previous pass. Approximates the live
 * count of the old (tenured) region so a minor pass can derive a total live
 * figure for gap adaptation without walking the old region. Old objects
 * freed by refcounting between passes make this stale-high, which only
 * inflates the next gap slightly; every major pass recomputes it exactly. */
static int64_t gc_old_live_count = 0;

static int gc_buffers_ensure(int32_t needed) {
    /* Ensure the candidate / worklist buffers can hold at least `needed`
     * candidates. Returns 0 on success, -1 on alloc fail. */
    if (gc_candidates_capacity >= needed && gc_candidates != NULL) {
        return 0;
    }
    int32_t new_cap = gc_candidates_capacity > 0 ? gc_candidates_capacity : GC_INITIAL_CAPACITY;
    while (new_cap < needed) {
        if (new_cap > INT32_MAX / 2) {
            /* About to overflow — clamp and let the caller cope. */
            new_cap = INT32_MAX - 1;
            break;
        }
        new_cap *= 2;
    }
    GCCandidate* nc = (GCCandidate*)realloc(gc_candidates, (size_t)new_cap * sizeof(GCCandidate));
    if (nc == NULL) return -1;
    gc_candidates = nc;

    ObjHeader** nw = (ObjHeader**)realloc(gc_worklist, (size_t)new_cap * sizeof(ObjHeader*));
    if (nw == NULL) return -1;
    gc_worklist = nw;
    gc_worklist_capacity = new_cap;
    gc_candidates_capacity = new_cap;
    return 0;
}

/* ---- GC trace callbacks ----
 *
 * Plumbing for the optional ``TypeDescriptor.trace_fn`` hook. Container
 * types (Vec/Dict) invoke these via their ``trace_fn`` so the cycle
 * collector can reach into a runtime-allocated buffer that the static
 * FieldDescriptor list cannot describe. The callbacks read and update
 * the same ``gc_candidates`` / ``gc_index`` state that the inline
 * FieldDescriptor walk uses.
 */
static void gc_subtract_cb(void* target, void* ctx) {
    (void)ctx;
    if (target == NULL) return;
    ObjHeader* th = OBJ_HEADER(target);
    if (th->rc & GC_CAND_BIT) {
        th->rc--;
    }
}

static void gc_mark_cb(void* target, void* ctx) {
    int32_t* work_count_p = (int32_t*)ctx;
    if (work_count_p == NULL || target == NULL) return;
    ObjHeader* th = OBJ_HEADER(target);
    if ((th->rc & GC_CAND_BIT) && !(th->rc & GC_REACH_BIT)) {
        th->rc |= GC_REACH_BIT;
        gc_worklist[(*work_count_p)++] = th;
    }
}

#ifdef ROLANG_GC_STATS
#include <stdio.h>
#include <stdlib.h>
static long gc_st_minors, gc_st_majors, gc_st_min_c, gc_st_maj_c, gc_st_min_l, gc_st_maj_l;
static int  gc_st_reg;
static void gc_stats_dump(void) {
    fprintf(stderr, "=== GC STATS ===\n");
    fprintf(stderr, "  minors=%ld cand_sum=%ld live_walked_sum=%ld\n",
            gc_st_minors, gc_st_min_c, gc_st_min_l);
    fprintf(stderr, "  majors=%ld cand_sum=%ld live_walked_sum=%ld\n",
            gc_st_majors, gc_st_maj_c, gc_st_maj_l);
}
void rt_gc_stats_record(int is_major, int live, int cands) {
    if (!gc_st_reg) { gc_st_reg = 1; atexit(gc_stats_dump); }
    if (is_major) { gc_st_majors++; gc_st_maj_c += cands; gc_st_maj_l += live; }
    else          { gc_st_minors++; gc_st_min_c += cands; gc_st_min_l += live; }
}
#endif

/**
 * Trigger cycle-detection GC.
 *
 * Only called periodically (e.g. every 10k allocations) or on explicit request.
 * Collects unreachable reference cycles that rt_obj_release can't detect.
 */
void rt_gc_collect(void) {
    /* Early return if not enough allocations since last collection.
     * Check without the full lock to avoid overhead on every GCCheck. */
    if (gc_alloc_counter - gc_last_collect_count < gc_next_gap) {
        return;
    }

    /* Re-entrancy guard: if a ``deinit`` running inside an active GC pass
     * triggers another ``rt_gc_collect`` (e.g. by allocating enough to push
     * gc_alloc_counter past the next threshold), we must NOT try to acquire
     * the spinlock we are already holding — that would deadlock. Every
     * other helper (gc_list_add, gc_list_remove, etc.) already short-circuits
     * when ``gc_running`` is set. */
    if (gc_running) {
        return;
    }

    while (atomic_flag_test_and_set_explicit(&gc_list_lock, memory_order_acquire)) {
        /* spin */
    }
    /* Double-check after acquiring the lock: another thread (in a future
     * multi-threaded runtime) might have set gc_running between our load
     * and the lock acquisition. Today this is impossible, but keeps the
     * invariant honest. */
    if (gc_running) {
        atomic_flag_clear_explicit(&gc_list_lock, memory_order_release);
        return;
    }
    gc_running = 1;

    /* Decide minor (young region only) vs major (whole list). The first
     * collection (gc_old_head == NULL) and every GC_MAJOR_EVERY-th minor are
     * major. `scan_end` bounds Step 1 to the young region for a minor. The
     * boundary is disabled (NULL) for the duration of the collection so
     * gc_list_remove no-ops on it, and re-established at the end — every
     * surviving object becomes old (tenured). Excluding old objects from a
     * minor's candidate set can only ADD apparent external references (an
     * old->young edge is not subtracted), so a minor under-collects at worst
     * (cross-generational / old-only cycles float until the next major) and can
     * never over-collect — the same safety property as the acyclic skip. */
    int is_major = (gc_old_head == NULL);
    if (!is_major && ++gc_minor_count >= GC_MAJOR_EVERY) is_major = 1;
    if (is_major) gc_minor_count = 0;
    ObjHeader* scan_end = is_major ? NULL : gc_old_head;
    gc_old_head = NULL;

    /* Step 1: Build the candidate set from the scanned region (young for a
     * minor, all for a major) in ONE walk, tagging each candidate's rc with
     * GC_CAND_BIT and counting the region's live objects as we go. The old
     * region's live count is carried over from the previous pass's survivor
     * count (gc_old_live_count), so no separate whole-list walk is needed.
     * Buffers grow on demand mid-walk; on allocation failure we proceed with
     * the partial candidate set — trial deletion over a subset treats refs
     * from outside the subset as external, so it under-collects at worst. */
    if (gc_buffers_ensure(GC_INITIAL_CAPACITY) != 0) {
        /* Out of memory creating the candidate buffer. Conservatively skip
         * this collection — better to leak a cycle than abort the program.
         * Restart the allocation clock: otherwise the trigger condition
         * stays permanently true and EVERY subsequent allocation re-enters
         * the collector (observed as a ~180x slowdown on allocation-heavy
         * acyclic workloads). */
        gc_old_head = gc_object_list;
        gc_last_collect_count = gc_alloc_counter;
        gc_trigger_at = gc_alloc_counter + gc_next_gap;
        gc_running = 0;
        atomic_flag_clear_explicit(&gc_list_lock, memory_order_release);
        return;
    }

    gc_candidate_count = 0;
    /* Whether ANY candidate's type declares a `deinit`. Collected objects are
     * always a subset of the candidates, so when this stays 0 the sweep can
     * skip the pin / deinit / resurrection phases and the defensive
     * field-nulling walk entirely (see Step 6). */
    int any_deinit = 0;
    int64_t young_live = 0;
    ObjHeader* obj = gc_object_list;
    /* SKIP the list head. The allocation-site trigger fires right after
     * gc_list_add, so the head is the object being allocated RIGHT NOW: for
     * a noinit allocation its payload is stale pool bytes (the caller's
     * field stores have not run yet), and the collector must never read
     * those as pointer fields. Excluding one object from candidacy only
     * under-collects (its refs into the candidate set are treated as
     * external), and the freshest allocation is never collectable anyway —
     * the constructing code holds its rc=1 reference. It still counts as
     * young+live for gap pacing. */
    if (obj != NULL && obj != scan_end) {
        if (obj->rc > 0) young_live++;
        obj = obj->next;
    }
    while (obj != scan_end) {
        if (obj->rc > 0) {
            young_live++;
            /* Acyclic-typed objects can never be part of a reference cycle, so
             * we exclude them from the candidate set. References *from* them to
             * candidates are then treated as external (keeping those candidates
             * alive), and the acyclic objects themselves are freed promptly by
             * refcounting in rt_obj_release, never by the collector. */
            TypeDescriptor* od = rt_get_type_descriptor(obj->type_id);
            if (od == NULL || !od->acyclic) {
                if (gc_candidate_count == gc_candidates_capacity &&
                    gc_buffers_ensure(gc_candidates_capacity + 1) != 0) {
                    break;  /* partial set: safe under-collection */
                }
                GCCandidate* c = &gc_candidates[gc_candidate_count++];
                c->obj = obj;
                c->saved_rc = obj->rc;
                c->collected = 0;
                obj->rc |= GC_CAND_BIT;
                if (od != NULL && od->deinit_fn != NULL) any_deinit = 1;
            }
        }
        obj = obj->next;
    }
    int64_t live_count = young_live + (is_major ? 0 : gc_old_live_count);

#ifdef ROLANG_GC_STATS
    {
        extern void rt_gc_stats_record(int is_major, int live, int cands);
        rt_gc_stats_record(is_major, (int)live_count, gc_candidate_count);
    }
#endif

    if (gc_candidate_count == 0) {
        gc_old_head = gc_object_list;   /* tenure: no cyclic candidates this pass */
        gc_old_live_count = live_count;
        /* Restart the allocation clock and adapt the gap exactly like the
         * normal epilogue (survivors == live_count: nothing was collected).
         * An all-acyclic live set hits this path on EVERY collection; without
         * the update the trigger stays armed and each subsequent allocation
         * pays a full collect preamble. */
        {
            int64_t gap = live_count * GC_GROWTH;
            if (gap < GC_MIN_GAP) gap = GC_MIN_GAP;
            if (gap > GC_MAX_GAP) gap = GC_MAX_GAP;
            gc_next_gap = gap;
        }
        gc_last_collect_count = gc_alloc_counter;
        gc_trigger_at = gc_alloc_counter + gc_next_gap;
        gc_running = 0;
        atomic_flag_clear_explicit(&gc_list_lock, memory_order_release);
        return;
    }

    /* Step 2: Subtract internal references (now O(n * avg_fields) total).
     *
     * Each candidate walks its static FieldDescriptor list AND, if the
     * type has a registered ``trace_fn`` (containers like Vec / Dict),
     * also walks dynamic pointers found inside an external buffer that
     * the static descriptor cannot see into. */
    for (int32_t i = 0; i < gc_candidate_count; i++) {
        ObjHeader* h = gc_candidates[i].obj;
        TypeDescriptor* desc = rt_get_type_descriptor(h->type_id);
        if (desc == NULL) continue;

        void* payload = OBJ_PAYLOAD(h);
        int32_t field_count = rt_get_field_count(desc);
        FieldDescriptor* fields = rt_get_field_descriptors(desc);

        if (field_count > 0 && fields != NULL) {
            int32_t enum_tag = 0;
            int32_t tag_available = 0;

            for (int32_t f = 0; f < field_count; f++) {
                FieldDescriptor* fd = &fields[f];
                if (fd->case_tag >= 0) {
                    if (!tag_available) {
                        enum_tag = *(int32_t*)payload;
                        tag_available = 1;
                    }
                    if ((int32_t)enum_tag != fd->case_tag) continue;
                }

                void* target = *(void**)((char*)payload + fd->offset);
                if (target == NULL) continue;

                ObjHeader* th = OBJ_HEADER(target);
                if (th->rc & GC_CAND_BIT) {
                    th->rc--;
                }
            }
        }

        if (desc->trace_fn != NULL) {
            desc->trace_fn(payload, gc_subtract_cb, NULL);
        }
    }

    /* Step 3: Mark every candidate reachable from an object that still has an
     * external reference. Trial deletion alone is not enough: if A has an
     * external ref and points to B, B's trial rc can fall to zero but B is
     * still reachable through A and must survive. */
    int32_t work_count = 0;
    for (int32_t i = 0; i < gc_candidate_count; i++) {
        ObjHeader* h = gc_candidates[i].obj;
        if ((h->rc & GC_RC_VALUE_MASK) > 0 && !(h->rc & GC_REACH_BIT)) {
            h->rc |= GC_REACH_BIT;
            gc_worklist[work_count++] = h;
        }
    }

    while (work_count > 0) {
        ObjHeader* h = gc_worklist[--work_count];
        TypeDescriptor* desc = rt_get_type_descriptor(h->type_id);
        if (desc == NULL) continue;

        void* payload = OBJ_PAYLOAD(h);
        int32_t field_count = rt_get_field_count(desc);
        FieldDescriptor* fields = rt_get_field_descriptors(desc);

        if (field_count > 0 && fields != NULL) {
            int32_t enum_tag = 0;
            int32_t tag_available = 0;

            for (int32_t f = 0; f < field_count; f++) {
                FieldDescriptor* fd = &fields[f];
                if (fd->case_tag >= 0) {
                    if (!tag_available) {
                        enum_tag = *(int32_t*)payload;
                        tag_available = 1;
                    }
                    if ((int32_t)enum_tag != fd->case_tag) continue;
                }

                void* target = *(void**)((char*)payload + fd->offset);
                if (target == NULL) continue;

                ObjHeader* th = OBJ_HEADER(target);
                if ((th->rc & GC_CAND_BIT) && !(th->rc & GC_REACH_BIT)) {
                    th->rc |= GC_REACH_BIT;
                    gc_worklist[work_count++] = th;
                }
            }
        }

        if (desc->trace_fn != NULL) {
            desc->trace_fn(payload, gc_mark_cb, &work_count);
        }
    }

    /* Step 4+5 (fused): identify garbage and restore survivor refcounts in
     * one scan. Restoring from saved_rc clears both GC bits on survivors;
     * collected objects keep their bits until pinned (deinit path) or freed. */
    int32_t collected_count = 0;
    for (int32_t i = 0; i < gc_candidate_count; i++) {
        GCCandidate* c = &gc_candidates[i];
        if (c->obj->rc & GC_REACH_BIT) {
            c->obj->rc = c->saved_rc;
        } else {
            c->collected = 1;
            collected_count++;
        }
    }

    /* Step 6: Collect garbage objects.
     *
     * Cleanup order is carefully chosen so user `deinit { ... }` blocks
     * observe the same well-defined state they get on the non-GC release
     * path: a fully-initialized `self` with intact pointer fields.
     *
     *   Phase 5a — Pin collected objects.
     *     Bump each collected object's rc to a high sentinel so any
     *     `rt_obj_retain` / `rt_obj_release` calls executed from inside
     *     a deinit (including a deinit that resurrects itself by stashing
     *     `self` somewhere reachable) cannot free it during the sweep.
     *
     *   Phase 5b — Run all deinits.
     *     Every deinit sees the full original graph, with pointer fields
     *     still pointing at the (still-valid) other collected objects.
     *     A deinit may mutate non-collected state freely.
     *
     *   Phase 5c — Resurrection check.
     *     If a deinit increased rc above the sentinel value, the object
     *     was published to surviving state. Skip its teardown so the
     *     surviving owner ends up with a live pointer.
     *
     *   Phase 5d — Unlink the (non-resurrected) collected objects from
     *     gc_object_list in one O(n) sweep.
     *
     *   Phase 5e — Release pointer fields and free memory.
     *     Field release happens *after* deinit so deinit could read them.
     */
    if (collected_count > 0) {
        gc_cycle_count += collected_count;

        /* Phases 5a-5c exist solely to give user `deinit` bodies a
         * well-defined view of the dying graph (and to catch resurrection).
         * When no candidate type has a deinit — the common case — they are
         * three wasted scans over the collected set; skip them outright. */
        if (any_deinit) {

        /* Phase 5a: Pin all collected objects so deinit-side ARC traffic
         * cannot cause an early free. INT64_MAX/2 leaves room for retains
         * (which bump rc up) without overflowing, and is large enough that
         * a saturating release won't drive it to 0. */
        const int64_t GC_PIN_RC = (int64_t)0x4000000000000000LL;
        for (int32_t i = 0; i < gc_candidate_count; i++) {
            GCCandidate* c = &gc_candidates[i];
            if (!c->collected) continue;
            __atomic_store_n(&c->obj->rc, GC_PIN_RC, __ATOMIC_RELAXED);
        }

        /* Phase 5b: Run all user deinits while every collected object is
         * still pinned and fully wired up. Deinit can freely call methods
         * on `self` and read any field. */
        for (int32_t i = 0; i < gc_candidate_count; i++) {
            GCCandidate* c = &gc_candidates[i];
            if (!c->collected) continue;
            ObjHeader* h = OBJ_HEADER(c->obj);
            TypeDescriptor* desc = rt_get_type_descriptor(h->type_id);
            if (desc != NULL && desc->deinit_fn != NULL) {
                desc->deinit_fn(c->obj);
            }
        }

        /* Phase 5c: Detect resurrections. A deinit that stored `self` into
         * a live container or global will have bumped rc above the pin.
         * Such objects must NOT be freed — restore their rc to the saved
         * external-reference count and skip the teardown. */
        for (int32_t i = 0; i < gc_candidate_count; i++) {
            GCCandidate* c = &gc_candidates[i];
            if (!c->collected) continue;
            int64_t cur = __atomic_load_n(&c->obj->rc, __ATOMIC_RELAXED);
            int64_t extra = cur - GC_PIN_RC;
            if (extra > 0) {
                /* Resurrected: restore observable rc to (saved + extra),
                 * leave object in gc_object_list, and skip free. */
                int64_t restored = (c->saved_rc > 0 ? c->saved_rc : 1) + extra;
                __atomic_store_n(&c->obj->rc, restored, __ATOMIC_RELAXED);
                c->collected = 0;
                collected_count--;
            }
        }

        } /* any_deinit */

        /* Phase 5d+5e (fused): for each collected object, release its
         * pointer fields and unlink it from gc_object_list in one scan, then
         * free everything in a second scan.
         *
         * Releases for ALL collected objects must complete before ANY of
         * them is freed: a closed cycle always has a back-edge to an
         * earlier-freed member, and releasing through it after the free
         * would scribble on the recycled pool slot (whose first word is the
         * free-list link). Hence release-all, then free-all.
         *
         * Unlinking inside the release scan is safe because nothing is freed
         * yet: a nested survivor teardown (a release driving a survivor's rc
         * to 0 calls rt_obj_release_slow -> gc_list_remove, which works under
         * gc_running) keeps the list consistent, and an already-unlinked
         * collected object's stale prev/next are never read again.
         * gc_old_head is NULL during a collection, so no boundary fixup. */
        if (any_deinit) {
        /* Deinit variant: collected objects are PINNED (rc ~= 2^62), so
         * "target is collected" is a simple magnitude test — survivors and
         * resurrected objects had their small exact rc restored. Null such
         * edges before the generic field release so it cannot decrement a
         * pinned rc or follow a dying edge. */
        const int64_t GC_PIN_TEST = (int64_t)1 << 61;
        for (int32_t i = 0; i < gc_candidate_count; i++) {
            GCCandidate* c = &gc_candidates[i];
            if (!c->collected) continue;
            ObjHeader* h = c->obj;
            TypeDescriptor* desc = rt_get_type_descriptor(h->type_id);
            if (desc != NULL) {
                int32_t field_count = rt_get_field_count(desc);
                FieldDescriptor* fields = rt_get_field_descriptors(desc);
                if (field_count > 0 && fields != NULL) {
                    void* payload = OBJ_PAYLOAD(h);
                    int32_t enum_tag = 0;
                    int32_t tag_available = 0;
                    for (int32_t f = 0; f < field_count; f++) {
                        FieldDescriptor* fd = &fields[f];
                        if (fd->case_tag >= 0) {
                            if (!tag_available) {
                                enum_tag = *(int32_t*)payload;
                                tag_available = 1;
                            }
                            if ((int32_t)enum_tag != fd->case_tag) continue;
                        }
                        void** field_ptr = (void**)((char*)payload + fd->offset);
                        void* target = *field_ptr;
                        if (target != NULL &&
                            OBJ_HEADER(target)->rc >= GC_PIN_TEST) {
                            /* Target is also collected — null the slot
                             * so we don't decrement its pinned rc. */
                            *field_ptr = NULL;
                        }
                    }
                }
            }
            obj_release_fields(h);
            ObjHeader* p = h->prev;
            ObjHeader* n = h->next;
            if (p != NULL) p->next = n;
            else           gc_object_list = n;
            if (n != NULL) n->prev = p;
        }
        } else {
        /* Fast variant (no deinits ran): the trial refcounts are exact, so
         * every collected object's rc is exactly 0 (plus stale GC bits) and
         * a release along a collected->collected edge merely perturbs a dead
         * rc — it can never re-enter the teardown path (which fires only on
         * the 1 -> 0 transition). No nulling walk needed: release every
         * collected object's fields directly via the codegen fast path. */
        for (int32_t i = 0; i < gc_candidate_count; i++) {
            GCCandidate* c = &gc_candidates[i];
            if (!c->collected) continue;
            ObjHeader* h = c->obj;
            TypeDescriptor* desc = rt_get_type_descriptor(h->type_id);
            if (desc != NULL && desc->release_fields_fn != NULL) {
                desc->release_fields_fn(OBJ_PAYLOAD(h));
            } else {
                obj_release_fields(h);
            }
            ObjHeader* p = h->prev;
            ObjHeader* n = h->next;
            if (p != NULL) p->next = n;
            else           gc_object_list = n;
            if (n != NULL) n->prev = p;
        }
        }

        /* Free scan, shared by both variants. */
        for (int32_t i = 0; i < gc_candidate_count; i++) {
            GCCandidate* c = &gc_candidates[i];
            if (!c->collected) continue;
            ObjHeader* h = c->obj;
            TypeDescriptor* desc = rt_get_type_descriptor(h->type_id);
            int64_t total = (desc != NULL) ? (OBJ_HEADER_SIZE + desc->payload_size) : 0;
            if (desc != NULL && total <= (int64_t)POOL_MAX_TOTAL_SIZE) {
                pool_free_object((void*)h, (size_t)total);
            } else {
                rt_free(h);
            }
        }
    }

    /* Adapt the next gap to the surviving live set so the next O(live) scan is
     * amortized over a proportional number of allocations, floored and capped.
     * `survivors` approximates objects still live after this pass: `live_count`
     * was the pre-pass live set and `collected_count` is how many were freed
     * (already net of any deinit resurrections from Phase 5c). We use total
     * live, not gc_candidate_count, so acyclic objects (excluded from
     * candidates) still count toward the heap size the gap is amortized
     * against — the allocation counter that drives the gap counts them too. */
    int64_t survivors = (int64_t)live_count - (int64_t)collected_count;
    int64_t gap = survivors * GC_GROWTH;
    if (gap < GC_MIN_GAP) gap = GC_MIN_GAP;
    if (gap > GC_MAX_GAP) gap = GC_MAX_GAP;
    gc_next_gap = gap;

    gc_last_collect_count = gc_alloc_counter;
    gc_trigger_at = gc_alloc_counter + gc_next_gap;
    /* Tenure: every object now in the list (all survivors, plus any allocated
     * by deinits during this pass) becomes old. The next minor scans only what
     * is allocated after this point. */
    gc_old_head = gc_object_list;
    gc_running = 0;
    atomic_flag_clear_explicit(&gc_list_lock, memory_order_release);
}

/**
 * Get the total number of typed-object allocations since program start.
 * Used by codegen to decide when to trigger GC.
 */
int64_t rt_obj_alloc_count(void) {
    int64_t count;
    gc_list_lock_acquire();
    count = gc_alloc_counter;
    gc_list_lock_release();
    return count;
}

/**
 * Number of typed objects currently live (in the GC list). Introspection for
 * leak tests: a workload that allocates then drops N objects should leave this
 * near its steady-state baseline, not growing with N.
 */
int64_t rt_obj_live_count(void) {
    int64_t n = 0;
    gc_list_lock_acquire();
    for (ObjHeader* o = gc_object_list; o != NULL; o = o->next) {
        if (o->rc > 0) n++;
    }
    gc_list_lock_release();
    return n;
}

/**
 * Get the number of objects collected by GC (for diagnostics).
 */
int64_t rt_gc_cycle_count(void) {
    return gc_cycle_count;
}

/* ============================================================================
 * String Operations
 *
 * String handles store { ptr data, i64 len }. Source-level `String` is now a
 * typed Rolang heap object whose payload contains one RawPtr handle.
 * ============================================================================ */

/* StringVal layout. Used for {data*, len} pairs in dict comparisons and
 * internal value-passing. The ARC-managed String object now stores the
 * StringVal inline in its payload (no intermediate heap handle). */
typedef struct { char* data; int64_t len; } StringVal;

/* Inline payload of an ARC-managed String object.
 * Must match the Rolang struct
 * `String { var data: RawPtr; var length: i64; var hash_cache: i64 }`
 * in std/string.rl AND the payload_size baked into the string-literal
 * emission in codegen/ops_memory.py.
 * `hash` is the lazily memoized key hash (0 = not computed yet); string
 * contents are immutable after construction so it never goes stale. */
typedef struct {
    char* data;
    int64_t len;
    int64_t hash;
} StringPayload;

static inline StringVal rt_string_empty_val(void) {
    return (StringVal){NULL, 0};
}

static inline StringPayload* rt_string_payload(const void* string_obj) {
    if (string_obj == NULL) return NULL;
    return (StringPayload*)OBJ_PAYLOAD((void*)string_obj);
}

static inline StringVal rt_string_obj_value(const void* string_obj) {
    StringPayload* sp = rt_string_payload(string_obj);
    if (sp == NULL || sp->data == NULL) return rt_string_empty_val();
    return (StringVal){sp->data, sp->len};
}

/* ============================================================================
 * Collections
 *
 * Dictionaries store byte copies of values via a linear-probe table.
 * ============================================================================ */

enum {
    RT_DICT_KEY_BYTES = 0,
    RT_DICT_KEY_STRING = 1
};

/*
 * Dictionary layout — CPython-style "compact dict":
 *
 *   1. ``entries`` is a packed, insertion-ordered array of (key, value)
 *      pairs occupying indices [0, len). Iteration walks 0..len.
 *   2. ``buckets`` is a parallel hash table (size = power of two) mapping
 *      a bucket slot to an index into ``entries`` (-1 = empty). Lookup
 *      and update use linear probing over this table.
 *
 * The single backing allocation lays out:
 *      [RolangDict header]
 *      [entries data ... capacity * (key_size + value_size)]
 *      [buckets   ... bucket_count * sizeof(int32_t)]
 *
 * Grow policy: keep buckets_count = next_pow2(capacity * 2). When the
 * load factor on buckets exceeds 0.75, both entries and buckets are
 * reallocated and re-indexed.
 *
 * ``key_type_id`` / ``value_type_id`` are type-descriptor indices:
 *   0 = primitive (no retain/release needed)
 *   non-zero = heap type (retain on insert, release on overwrite/free)
 *
 * Note: dict remove is not exposed by the language yet, so we never
 * generate tombstones — this keeps probing simple.
 */
typedef struct RolangDict {
    int64_t len;
    int64_t capacity;          /* entries array size */
    int64_t key_size;
    int64_t value_size;
    int32_t key_kind;
    int32_t key_type_id;
    int32_t value_type_id;
    int32_t bucket_count;      /* must be a power of two; -1 in empty hashes */
    int64_t buckets_offset;    /* byte offset from data[] to buckets[] */
    unsigned char data[];      /* entries followed by buckets */
} RolangDict;

#define RT_DICT_BUCKET_EMPTY (-1)

/* Bucket slot: entry index + 32-bit hash tag. The tag is the HIGH half of the
 * 64-bit key hash (the bucket position uses the low bits, so the two are
 * decorrelated). Probing compares tags first and only dereferences/compares
 * the actual keys on a tag match, which removes nearly every key memcmp from
 * the probe loop — the dominant cost in string-keyed hot loops (word_freq). */
typedef struct {
    int32_t  idx;   /* index into entries[], or RT_DICT_BUCKET_EMPTY */
    uint32_t tag;   /* high 32 bits of the key hash; undefined when empty */
} DictBucket;

/* memcpy with the common tiny sizes peeled into constant-size copies the
 * compiler lowers to single load/store pairs. Collection elements are almost
 * always 1/2/4/8/16 bytes (primitives, object pointers, StringVal pairs);
 * a variable-length memcpy is a libcall on every get/set. */
static inline void _rt_copy_small(void* dst, const void* src, size_t n) {
    switch (n) {
    case 1:  memcpy(dst, src, 1);  return;
    case 2:  memcpy(dst, src, 2);  return;
    case 4:  memcpy(dst, src, 4);  return;
    case 8:  memcpy(dst, src, 8);  return;
    case 16: memcpy(dst, src, 16); return;
    default: memcpy(dst, src, n);  return;
    }
}

static inline size_t _dict_stride(const RolangDict* dict) {
    return (size_t)dict->key_size + (size_t)dict->value_size;
}

static unsigned char* rt_dict_key_at(RolangDict* dict, int64_t index) {
    return dict->data + ((size_t)index * _dict_stride(dict));
}

static unsigned char* rt_dict_value_at(RolangDict* dict, int64_t index) {
    return rt_dict_key_at(dict, index) + (size_t)dict->key_size;
}

static DictBucket* _dict_buckets(RolangDict* dict) {
    return (DictBucket*)(dict->data + (size_t)dict->buckets_offset);
}

/* Byte equality without the memcmp libcall for short runs. Keys in hash-table
 * hot loops are typically a handful of bytes; the call overhead of memcmp
 * dwarfs the comparison itself. Overlapping word loads cover 4..16 bytes in
 * two compares; memcpy compiles to plain loads at -O1+. */
static inline int _dict_bytes_equal(const unsigned char* a,
                                    const unsigned char* b, size_t n) {
    if (n >= 16) {
        return memcmp(a, b, n) == 0;
    }
    if (n >= 8) {
        uint64_t x0, y0, x1, y1;
        memcpy(&x0, a, 8);
        memcpy(&y0, b, 8);
        memcpy(&x1, a + n - 8, 8);
        memcpy(&y1, b + n - 8, 8);
        return ((x0 ^ y0) | (x1 ^ y1)) == 0;
    }
    if (n >= 4) {
        uint32_t x0, y0, x1, y1;
        memcpy(&x0, a, 4);
        memcpy(&y0, b, 4);
        memcpy(&x1, a + n - 4, 4);
        memcpy(&y1, b + n - 4, 4);
        return ((x0 ^ y0) | (x1 ^ y1)) == 0;
    }
    for (size_t i = 0; i < n; i++) {
        if (a[i] != b[i]) return 0;
    }
    return 1;
}

__attribute__((always_inline))
static inline int rt_dict_keys_equal(RolangDict* dict, const void* lhs, const void* rhs) {
    if (dict->key_kind == RT_DICT_KEY_STRING) {
        StringVal a_val;
        StringVal b_val;
        if (dict->key_type_id != 0) {
            a_val = rt_string_obj_value(*(void* const*)lhs);
            b_val = rt_string_obj_value(*(void* const*)rhs);
        } else {
            a_val = *(const StringVal*)lhs;
            b_val = *(const StringVal*)rhs;
        }
        const StringVal* a = &a_val;
        const StringVal* b = &b_val;

        if (a->len != b->len) {
            return 0;
        }
        if (a->len == 0) {
            return 1;
        }
        if (a->data == NULL || b->data == NULL) {
            return a->data == b->data;
        }
        return _dict_bytes_equal((const unsigned char*)a->data,
                                 (const unsigned char*)b->data, (size_t)a->len);
    }

    return _dict_bytes_equal((const unsigned char*)lhs,
                             (const unsigned char*)rhs, (size_t)dict->key_size);
}

/* Word-at-a-time multiply-xor hash.
 * Replaces byte-at-a-time FNV-1a: same interface, ~8x fewer multiplies on
 * long keys and a single data multiply for the <=8-byte keys that dominate
 * dict-as-counter workloads. The multiply pushes entropy into the HIGH bits;
 * the closing `h ^= h >> 32` folds those well-mixed bits back into the low
 * bits that feed the pow2 bucket mask. The tag (high 32 bits) is mixed by
 * the multiply alone. Kept deliberately short: hash latency sits on the
 * critical path of every dict lookup, and a full splitmix finalizer costs
 * more than it buys at hash-table quality levels. */
static inline uint64_t _dict_hash_bytes(const unsigned char* p, size_t n) {
    const uint64_t K = 0x9e3779b97f4a7c15ULL;
    uint64_t h = 0xcbf29ce484222325ULL ^ ((uint64_t)n * K);
    if (n > 8) {
        do {
            uint64_t w;
            memcpy(&w, p, 8);
            h = (h ^ w) * K;
            p += 8;
            n -= 8;
        } while (n >= 8);
        if (n > 0) {
            /* Overlapping tail load: total length > 8, so p+n-8 is valid. */
            uint64_t w;
            memcpy(&w, p + n - 8, 8);
            h = (h ^ w) * K;
        }
    } else if (n > 0) {
        uint64_t w;
        if (n == 8) {
            memcpy(&w, p, 8);
        } else if (n >= 4) {
            /* Two overlapping 4-byte loads cover 4..7 bytes. */
            uint32_t lo, hi;
            memcpy(&lo, p, 4);
            memcpy(&hi, p + n - 4, 4);
            w = (uint64_t)lo | ((uint64_t)hi << 32);
        } else {
            /* 1..3 bytes: independent loads, no carried dependency. */
            w = (uint64_t)p[0]
              | ((uint64_t)p[n >> 1] << 8)
              | ((uint64_t)p[n - 1] << 16);
        }
        h = (h ^ w) * K;
    }
    h ^= h >> 32;
    return h;
}

__attribute__((always_inline))
static inline uint64_t _dict_hash_key(RolangDict* dict, const void* key) {
    if (dict->key_kind == RT_DICT_KEY_STRING) {
        if (dict->key_type_id != 0) {
            /* Heap String object: memoize the hash in the object itself.
             * Hot dict workloads look the same few key objects up millions
             * of times (e.g. a pre-built key vector); after the first probe
             * the hash is a single load instead of a recompute. 0 means
             * "not computed" — a computed hash is forced non-zero. */
            void* obj = *(void* const*)key;
            StringPayload* sp = rt_string_payload(obj);
            if (sp == NULL) {
                return _dict_hash_bytes(NULL, 0);
            }
            uint64_t cached = (uint64_t)sp->hash;
            if (cached != 0) {
                return cached;
            }
            uint64_t h = (sp->data == NULL || sp->len <= 0)
                ? _dict_hash_bytes(NULL, 0)
                : _dict_hash_bytes((const unsigned char*)sp->data, (size_t)sp->len);
            if (h == 0) h = 0x9e3779b97f4a7c15ULL;
            sp->hash = (int64_t)h;
            return h;
        }
        StringVal s_val = *(const StringVal*)key;
        if (s_val.data == NULL || s_val.len <= 0) {
            return _dict_hash_bytes(NULL, 0);
        }
        return _dict_hash_bytes((const unsigned char*)s_val.data, (size_t)s_val.len);
    }
    return _dict_hash_bytes((const unsigned char*)key, (size_t)dict->key_size);
}

static int64_t _next_pow2_at_least(int64_t v) {
    if (v < 1) return 1;
    int64_t out = 1;
    while (out < v) out *= 2;
    return out;
}

/* ---- retain / release helpers for dict keys and values ---- */

static inline void _dict_retain_element(int32_t type_id, const void* ptr) {
    if (type_id == 0 || ptr == NULL) return;
    void* obj = *(void**)ptr;
    if (obj != NULL) rt_obj_retain(obj);
}

static inline void _dict_release_element(int32_t type_id, void* ptr) {
    if (type_id == 0 || ptr == NULL) return;
    void* obj = *(void**)ptr;
    if (obj != NULL) rt_obj_release(obj);
}

static void _dict_release_all(RolangDict* dict) {
    if (dict->key_type_id != 0 || dict->value_type_id != 0) {
        for (int64_t i = 0; i < dict->len; i++) {
            _dict_release_element(dict->key_type_id, rt_dict_key_at(dict, i));
            _dict_release_element(dict->value_type_id, rt_dict_value_at(dict, i));
        }
    }
}

static size_t _dict_alloc_size(int64_t capacity, int64_t key_size, int64_t value_size,
                                int64_t bucket_count) {
    size_t entries = (size_t)capacity * ((size_t)key_size + (size_t)value_size);
    /* Align bucket table to 4 bytes (DictBucket is two 4-byte fields). */
    entries = (entries + 3u) & ~(size_t)3u;
    return sizeof(RolangDict) + entries + (size_t)bucket_count * sizeof(DictBucket);
}

static void _dict_clear_buckets(RolangDict* dict) {
    if (dict->bucket_count <= 0) return;
    DictBucket* buckets = _dict_buckets(dict);
    for (int64_t i = 0; i < dict->bucket_count; i++) {
        buckets[i].idx = RT_DICT_BUCKET_EMPTY;
        buckets[i].tag = 0;
    }
}

/* Rebuild buckets[] from the entries array. */
static void _dict_rehash(RolangDict* dict) {
    _dict_clear_buckets(dict);
    if (dict->bucket_count <= 0) return;
    DictBucket* buckets = _dict_buckets(dict);
    uint64_t mask = (uint64_t)(dict->bucket_count - 1);
    for (int64_t i = 0; i < dict->len; i++) {
        uint64_t hash = _dict_hash_key(dict, rt_dict_key_at(dict, i));
        uint64_t h = hash & mask;
        while (buckets[h].idx != RT_DICT_BUCKET_EMPTY) {
            h = (h + 1) & mask;
        }
        buckets[h].idx = (int32_t)i;
        buckets[h].tag = (uint32_t)(hash >> 32);
    }
}

/* ---- public API ---- */

void* rt_dict_new(int64_t capacity, int64_t key_size, int64_t value_size,
                  int32_t key_kind, int32_t key_type_id, int32_t value_type_id) {
    if (capacity < 0 || key_size < 0 || value_size < 0) {
        return NULL;
    }

    if (capacity < 8) capacity = 8;
    int64_t bucket_count = _next_pow2_at_least(capacity * 4);

    size_t total = _dict_alloc_size(capacity, key_size, value_size, bucket_count);
    RolangDict* dict = (RolangDict*)malloc(total);
    if (dict == NULL) {
        return NULL;
    }

    dict->len = 0;
    dict->capacity = capacity;
    dict->key_size = key_size;
    dict->value_size = value_size;
    dict->key_kind = key_kind;
    dict->key_type_id = key_type_id;
    dict->value_type_id = value_type_id;
    dict->bucket_count = (int32_t)bucket_count;

    size_t entries_bytes = (size_t)capacity * ((size_t)key_size + (size_t)value_size);
    size_t entries_aligned = (entries_bytes + 3u) & ~(size_t)3u;
    dict->buckets_offset = (int64_t)entries_aligned;
    if (entries_bytes > 0) {
        memset(dict->data, 0, entries_bytes);
    }
    _dict_clear_buckets(dict);

    return dict;
}

void* rt_dict_resize(void* dict_ptr, int64_t new_capacity) {
    if (!dict_ptr || new_capacity <= 0) return dict_ptr;
    RolangDict* dict = (RolangDict*)dict_ptr;
    if (new_capacity <= dict->capacity) return dict_ptr;

    int64_t new_bucket_count = _next_pow2_at_least(new_capacity * 4);
    size_t total = _dict_alloc_size(new_capacity, dict->key_size, dict->value_size,
                                    new_bucket_count);
    RolangDict* new_dict = (RolangDict*)malloc(total);
    if (!new_dict) return dict_ptr;

    *new_dict = *dict;  /* copy header (will fix offsets below) */
    new_dict->capacity = new_capacity;
    new_dict->bucket_count = (int32_t)new_bucket_count;
    size_t entries_bytes = (size_t)new_capacity *
        ((size_t)new_dict->key_size + (size_t)new_dict->value_size);
    size_t entries_aligned = (entries_bytes + 3u) & ~(size_t)3u;
    new_dict->buckets_offset = (int64_t)entries_aligned;

    if (dict->len > 0) {
        memcpy(new_dict->data, dict->data,
               (size_t)dict->len * _dict_stride(dict));
    }
    if (entries_bytes > (size_t)dict->len * _dict_stride(dict)) {
        memset(new_dict->data + (size_t)dict->len * _dict_stride(dict), 0,
               entries_bytes - (size_t)dict->len * _dict_stride(dict));
    }

    _dict_rehash(new_dict);
    /* No retain/release here — entries are byte-copied, including the
     * embedded pointers; their refcounts stay correct. */
    free(dict);
    return new_dict;
}

/* Look up an entry index for ``key`` and return -1 on miss.
 * On hit, ``*out_bucket`` is the bucket slot that holds the index;
 * on miss, ``*out_bucket`` is the slot where a fresh entry should land.
 * ``*out_tag`` is always set to the key's bucket tag so insert paths can
 * record it without rehashing. */
__attribute__((always_inline))
static inline int32_t _dict_probe(RolangDict* dict, const void* key,
                                  uint64_t* out_bucket, uint32_t* out_tag) {
    DictBucket* buckets = _dict_buckets(dict);
    uint64_t mask = (uint64_t)(dict->bucket_count - 1);
    uint64_t hash = _dict_hash_key(dict, key);
    uint32_t tag = (uint32_t)(hash >> 32);
    uint64_t h = hash & mask;
    *out_tag = tag;
    for (;;) {
        DictBucket b = buckets[h];
        if (b.idx == RT_DICT_BUCKET_EMPTY) {
            *out_bucket = h;
            return -1;
        }
        if (b.tag == tag) {
            unsigned char* existing_key = rt_dict_key_at(dict, b.idx);
            if (rt_dict_keys_equal(dict, existing_key, key)) {
                *out_bucket = h;
                return b.idx;
            }
        }
        h = (h + 1) & mask;
    }
}

void* rt_dict_set(void* dict_ptr, const void* key, const void* value) {
    if (dict_ptr == NULL || key == NULL || value == NULL) {
        return dict_ptr;
    }

    RolangDict* dict = (RolangDict*)dict_ptr;
    uint64_t bucket;
    uint32_t tag;
    int32_t idx = _dict_probe(dict, key, &bucket, &tag);
    if (idx >= 0) {
        /* Update existing value: release old, copy new, retain new. */
        unsigned char* slot = rt_dict_value_at(dict, idx);
        _dict_release_element(dict->value_type_id, slot);
        memcpy(slot, value, (size_t)dict->value_size);
        _dict_retain_element(dict->value_type_id, slot);
        return dict_ptr;
    }

    /* Grow when entries or buckets are too tight. */
    int needs_grow = (dict->len + 1) >= dict->capacity
        || (dict->len + 1) * 4 > (int64_t)dict->bucket_count * 3;
    if (needs_grow) {
        int64_t new_cap = dict->capacity * 2;
        if (new_cap < 16) new_cap = 16;
        void* new_ptr = rt_dict_resize(dict_ptr, new_cap);
        if (new_ptr == dict_ptr) {
            return dict_ptr;  /* Resize failed; bail out. */
        }
        dict = (RolangDict*)new_ptr;
        dict_ptr = new_ptr;
        idx = _dict_probe(dict, key, &bucket, &tag);
        if (idx >= 0) {
            unsigned char* slot = rt_dict_value_at(dict, idx);
            _dict_release_element(dict->value_type_id, slot);
            memcpy(slot, value, (size_t)dict->value_size);
            _dict_retain_element(dict->value_type_id, slot);
            return dict_ptr;
        }
    }

    /* Append new entry and record it in the bucket. */
    int64_t new_idx = dict->len;
    unsigned char* dest_key = rt_dict_key_at(dict, new_idx);
    unsigned char* dest_value = rt_dict_value_at(dict, new_idx);
    memcpy(dest_key, key, (size_t)dict->key_size);
    memcpy(dest_value, value, (size_t)dict->value_size);
    _dict_retain_element(dict->key_type_id, dest_key);
    _dict_retain_element(dict->value_type_id, dest_value);

    DictBucket* buckets = _dict_buckets(dict);
    buckets[bucket].idx = (int32_t)new_idx;
    buckets[bucket].tag = tag;
    dict->len++;
    return dict_ptr;
}

int32_t rt_dict_get(void* dict_ptr, const void* key, void* out) {
    if (dict_ptr == NULL || key == NULL || out == NULL) {
        return 0;
    }

    RolangDict* dict = (RolangDict*)dict_ptr;
    if (dict->len == 0) {
        memset(out, 0, (size_t)dict->value_size);
        return 0;
    }
    uint64_t bucket;
    uint32_t tag;
    int32_t idx = _dict_probe(dict, key, &bucket, &tag);
    if (idx >= 0) {
        _rt_copy_small(out, rt_dict_value_at(dict, idx), (size_t)dict->value_size);
        /* Retain heap-typed values for the caller. Without this the dict
         * still owns the slot but the caller's `out` would drop without
         * having been retained, causing a UAF the next time the dict is
         * read or destroyed. */
        _dict_retain_element(dict->value_type_id, out);
        return 1;
    }
    memset(out, 0, (size_t)dict->value_size);
    return 0;
}

/* Probe-or-insert in a SINGLE hash+probe: ensure `key` is present (inserting a
 * copy of *default_value if absent — a NULL default zero-fills) and write the
 * entry's array index to *out_index (an int64_t). Returns the (possibly resized)
 * dict pointer. Pair with rt_dict_get_at / rt_dict_set_at, whose index access is
 * O(1) and hash-free, so a read-modify-write (e.g. dict-as-counter: read count,
 * write count+1) pays ONE probe instead of the two that get()+set() cost. Entry
 * indices are stable: entries are append-only and resize preserves their order
 * (the dict has no remove), so an index stays valid until the next mutation. */
void* rt_dict_entry_index(void* dict_ptr, const void* key,
                          const void* default_value, void* out_index) {
    if (out_index == NULL) {
        return dict_ptr;
    }
    if (dict_ptr == NULL || key == NULL) {
        *(int64_t*)out_index = -1;
        return dict_ptr;
    }

    RolangDict* dict = (RolangDict*)dict_ptr;
    uint64_t bucket;
    uint32_t tag;
    int32_t idx = _dict_probe(dict, key, &bucket, &tag);
    if (idx >= 0) {
        *(int64_t*)out_index = idx;
        return dict_ptr;
    }

    /* Absent: grow if tight, then append (mirrors rt_dict_set's insert path). */
    int needs_grow = (dict->len + 1) >= dict->capacity
        || (dict->len + 1) * 4 > (int64_t)dict->bucket_count * 3;
    if (needs_grow) {
        int64_t new_cap = dict->capacity * 2;
        if (new_cap < 16) new_cap = 16;
        void* new_ptr = rt_dict_resize(dict_ptr, new_cap);
        if (new_ptr == dict_ptr) {
            *(int64_t*)out_index = -1;  /* resize failed */
            return dict_ptr;
        }
        dict = (RolangDict*)new_ptr;
        dict_ptr = new_ptr;
        idx = _dict_probe(dict, key, &bucket, &tag);
        if (idx >= 0) {
            *(int64_t*)out_index = idx;  /* defensive; was absent pre-resize */
            return dict_ptr;
        }
    }

    int64_t new_idx = dict->len;
    unsigned char* dest_key = rt_dict_key_at(dict, new_idx);
    unsigned char* dest_value = rt_dict_value_at(dict, new_idx);
    memcpy(dest_key, key, (size_t)dict->key_size);
    if (default_value != NULL) {
        memcpy(dest_value, default_value, (size_t)dict->value_size);
    } else {
        memset(dest_value, 0, (size_t)dict->value_size);
    }
    _dict_retain_element(dict->key_type_id, dest_key);
    _dict_retain_element(dict->value_type_id, dest_value);

    DictBucket* buckets = _dict_buckets(dict);
    buckets[bucket].idx = (int32_t)new_idx;
    buckets[bucket].tag = tag;
    dict->len++;
    *(int64_t*)out_index = new_idx;
    return dict_ptr;
}

/* O(1) value read by entry index (no hash/probe). Retains heap-typed values for
 * the caller, exactly like rt_dict_get. `index` must come from rt_dict_entry_index
 * (or a dict iteration) and be valid for the current dict. */
void rt_dict_get_at(void* dict_ptr, int64_t index, void* out) {
    if (dict_ptr == NULL || out == NULL) {
        return;
    }
    RolangDict* dict = (RolangDict*)dict_ptr;
    if (index < 0 || index >= dict->len) {
        memset(out, 0, (size_t)dict->value_size);
        return;
    }
    _rt_copy_small(out, rt_dict_value_at(dict, index), (size_t)dict->value_size);
    _dict_retain_element(dict->value_type_id, out);
}

/* O(1) value write by entry index (no hash/probe). Releases the old value and
 * retains the new, exactly like rt_dict_set's update path. */
void rt_dict_set_at(void* dict_ptr, int64_t index, const void* value) {
    if (dict_ptr == NULL || value == NULL) {
        return;
    }
    RolangDict* dict = (RolangDict*)dict_ptr;
    if (index < 0 || index >= dict->len) {
        return;
    }
    unsigned char* slot = rt_dict_value_at(dict, index);
    _dict_release_element(dict->value_type_id, slot);
    _rt_copy_small(slot, value, (size_t)dict->value_size);
    _dict_retain_element(dict->value_type_id, slot);
}

int64_t rt_dict_len(void* dict_ptr) {
    if (dict_ptr == NULL) {
        return 0;
    }

    RolangDict* dict = (RolangDict*)dict_ptr;
    return dict->len;
}

void* rt_dict_key_ptr(void* dict_ptr, int64_t index) {
    if (!dict_ptr || index < 0) return NULL;
    RolangDict* dict = (RolangDict*)dict_ptr;
    if (index >= dict->len) return NULL;
    return rt_dict_key_at(dict, index);
}

/* Copy the key at `index` into `out`, retaining heap-typed keys.
 * Mirrors rt_gvec_get: the caller receives a fresh strong reference for
 * heap keys, and raw bytes for primitive keys. Safe for DictIter.__next__. */
void rt_dict_key_copy(void* dict_ptr, int64_t index, void* out) {
    if (!dict_ptr || !out) return;
    RolangDict* dict = (RolangDict*)dict_ptr;
    if (index < 0 || index >= dict->len) return;
    unsigned char* src = rt_dict_key_at(dict, index);
    memcpy(out, src, (size_t)dict->key_size);
    _dict_retain_element(dict->key_type_id, out);
}

/* Pointer to the value slot at `index` (0 <= index < rt_dict_len).
 * Used by Dict iteration in std/iter.rl. Mirrors rt_dict_key_ptr. */
void* rt_dict_value_ptr(void* dict_ptr, int64_t index) {
    if (!dict_ptr || index < 0) return NULL;
    RolangDict* dict = (RolangDict*)dict_ptr;
    if (index >= dict->len) return NULL;
    return rt_dict_value_at(dict, index);
}

void rt_dict_free(void* dict_ptr) {
    if (!dict_ptr) return;
    RolangDict* dict = (RolangDict*)dict_ptr;
    _dict_release_all(dict);
    free(dict_ptr);
}

/*
 * GC trace hook for ``Dict<K, V>``. Mirrors :func:`rt_gvec_gc_trace`:
 * codegen installs this on every monomorphized ``Dict_*`` type's
 * ``TypeDescriptor.trace_fn``. The payload's first 8 bytes are the
 * ``handle: RawPtr`` that points at a :type:`RolangDict`.
 *
 * Heap-typed keys and values are stored interleaved in the dict's
 * ``data[]`` buffer; ``key_type_id`` / ``value_type_id`` are non-zero
 * when the corresponding slot needs to be traced (matching the
 * retain/release convention used elsewhere in this file).
 */
void rt_dict_gc_trace(void* payload, GCTraceCb cb, void* ctx) {
    if (payload == NULL || cb == NULL) return;
    void* handle = *(void**)payload;
    if (handle == NULL) return;

    RolangDict* d = (RolangDict*)handle;
    if (d->key_type_id == 0 && d->value_type_id == 0) return;

    for (int64_t i = 0; i < d->len; i++) {
        if (d->key_type_id != 0) {
            void* key_obj = *(void**)rt_dict_key_at(d, i);
            if (key_obj != NULL) cb(key_obj, ctx);
        }
        if (d->value_type_id != 0) {
            void* value_obj = *(void**)rt_dict_value_at(d, i);
            if (value_obj != NULL) cb(value_obj, ctx);
        }
    }
}

/* ============================================================================
 * Async Runtime — single-threaded cooperative multitasking
 *
 * Task structure:
 * {
 *     void* frame;           // Coroutine frame (state + locals)
 *     void (*resume)(void*); // Resume function pointer
 *     int32_t completed;     // Completion flag
 *     void* result;          // Result value
 * }
 * ============================================================================ */

typedef struct TaskHandle {
    void* frame;
    void (*resume_fn)(void*);
    int32_t completed;
    int32_t result_kind;
    void* result;
} TaskHandle;

enum {
    RT_TASK_RESULT_NONE = 0,
    RT_TASK_RESULT_BOX = 1,
    RT_TASK_RESULT_HEAP_REF = 2
};

/*
 * Dynamically growable ring buffer for the single-threaded task scheduler.
 * Starts at TASK_QUEUE_INITIAL_CAPACITY and doubles on demand. The previous
 * implementation had a hard cap of 256 tasks; in practice we want any
 * realistic workload (parser fan-out, fetch fan-out, etc.) to just work.
 */
#define TASK_QUEUE_INITIAL_CAPACITY 256
static TaskHandle** task_queue = NULL;
static int task_queue_capacity = 0;
static int task_queue_head = 0;
static int task_queue_tail = 0;

/* Thread-local current task for cooperative yield */
#if defined(__STDC_VERSION__) && (__STDC_VERSION__ >= 201112L) && !defined(__STDC_NO_THREADS__)
  #include <threads.h>
  static _Thread_local TaskHandle* current_task = NULL;
#else
  /* Fallback for older C standards - not thread-safe but works for single-threaded */
  static TaskHandle* current_task = NULL;
#endif

static int task_queue_count(void) {
    if (task_queue_capacity == 0) return 0;
    return (task_queue_tail - task_queue_head + task_queue_capacity) % task_queue_capacity;
}

static int task_queue_ensure_capacity(void) {
    if (task_queue == NULL) {
        task_queue_capacity = TASK_QUEUE_INITIAL_CAPACITY;
        task_queue = (TaskHandle**)malloc(sizeof(TaskHandle*) * (size_t)task_queue_capacity);
        if (task_queue == NULL) {
            task_queue_capacity = 0;
            return -1;
        }
        task_queue_head = 0;
        task_queue_tail = 0;
        return 0;
    }

    /* Grow when the ring would wrap into the head pointer. */
    if (((task_queue_tail + 1) % task_queue_capacity) != task_queue_head) {
        return 0;
    }

    int old_capacity = task_queue_capacity;
    int new_capacity = old_capacity * 2;
    TaskHandle** new_queue = (TaskHandle**)malloc(sizeof(TaskHandle*) * (size_t)new_capacity);
    if (new_queue == NULL) {
        return -1;
    }
    /* Linearise the ring into the new buffer starting at index 0. */
    int count = task_queue_count();
    for (int i = 0; i < count; i++) {
        new_queue[i] = task_queue[(task_queue_head + i) % old_capacity];
    }
    free(task_queue);
    task_queue = new_queue;
    task_queue_capacity = new_capacity;
    task_queue_head = 0;
    task_queue_tail = count;
    return 0;
}

/**
 * Push a task onto the queue. Returns 0 on success, -1 on allocation failure.
 */
static int task_queue_push(TaskHandle* task) {
    if (task_queue_ensure_capacity() != 0) {
        fprintf(stderr, "rolang runtime error: async task queue out of memory\n");
        return -1;
    }
    task_queue[task_queue_tail] = task;
    task_queue_tail = (task_queue_tail + 1) % task_queue_capacity;
    return 0;
}

static TaskHandle* task_queue_pop(void) {
    if (task_queue == NULL || task_queue_head == task_queue_tail) {
        return NULL; // Empty
    }
    TaskHandle* task = task_queue[task_queue_head];
    task_queue_head = (task_queue_head + 1) % task_queue_capacity;
    return task;
}

/**
 * Yield CPU to the OS scheduler. Portable fallback.
 */
static void cpu_yield(void) {
#if defined(__linux__) || defined(__APPLE__)
    sched_yield();
#else
    /* No-op on platforms without sched_yield */
#endif
}

/**
 * Allocate a coroutine frame.
 *
 * @param size Size of the frame in bytes
 * @return Pointer to the allocated frame
 */
void* rt_frame_alloc(int64_t size) {
    return malloc((size_t)size);
}

/**
 * Free a coroutine frame.
 *
 * @param frame Pointer to the frame
 */
void rt_frame_free(void* frame) {
    if (frame != NULL) {
        free(frame);
    }
}

/**
 * Spawn a new async task.
 *
 * Creates a TaskHandle and schedules the task for execution.
 *
 * @param resume_fn The resume function for this coroutine
 * @param frame The coroutine frame
 * @return Pointer to the new TaskHandle
 */
TaskHandle* rt_task_spawn(void (*resume_fn)(void*), void* frame) {
    TaskHandle* task = (TaskHandle*)malloc(sizeof(TaskHandle));
    if (task == NULL) {
        return NULL;
    }

    task->frame = frame;
    task->resume_fn = resume_fn;
    task->completed = 0;
    task->result_kind = RT_TASK_RESULT_NONE;
    task->result = NULL;

    // Add to ready queue
    if (task_queue_push(task) != 0) {
        if (frame != NULL) rt_obj_release(frame);
        free(task);
        return NULL;
    }

    return task;
}

/**
 * Join (await) a task.
 *
 * If the task is not complete, runs the scheduler until it is.
 *
 * @param handle The task to join
 * @return The task's result value
 */
void* rt_task_join(TaskHandle* handle) {
    if (handle == NULL) {
        return NULL;
    }

    while (!handle->completed) {
        TaskHandle* ready_task = task_queue_pop();
        if (ready_task != NULL && !ready_task->completed) {
            ready_task->resume_fn(ready_task->frame);
            if (!ready_task->completed) {
                task_queue_push(ready_task);
            }
        } else {
            /* No runnable tasks — yield CPU instead of busy-waiting */
            cpu_yield();
        }
    }

    return handle->result;
}

/**
 * Mark a task as complete.
 *
 * @param handle The task handle
 * @param result The result value
 */
void rt_task_complete(TaskHandle* handle, void* result) {
    if (handle == NULL) {
        return;
    }

    handle->result = result;
    handle->result_kind = RT_TASK_RESULT_NONE;
    handle->completed = 1;
}

void rt_task_complete_owned(TaskHandle* handle, void* result, int32_t result_kind) {
    if (handle == NULL) {
        return;
    }
    handle->result = result;
    handle->result_kind = result_kind;
    handle->completed = 1;
}

void* rt_task_take_result(TaskHandle* handle) {
    if (handle == NULL) {
        return NULL;
    }
    void* result = handle->result;
    handle->result = NULL;
    handle->result_kind = RT_TASK_RESULT_NONE;
    return result;
}

void rt_task_destroy(TaskHandle* handle) {
    if (handle == NULL) {
        return;
    }
    if (!handle->completed) {
        rt_task_join(handle);
    }
    if (handle->result != NULL) {
        if (handle->result_kind == RT_TASK_RESULT_BOX) {
            rt_free(handle->result);
        } else if (handle->result_kind == RT_TASK_RESULT_HEAP_REF) {
            rt_obj_release(handle->result);
        }
        handle->result = NULL;
    }
    if (handle->frame != NULL) {
        rt_obj_release(handle->frame);
        handle->frame = NULL;
    }
    free(handle);
}

/**
 * Yield control to the scheduler.
 *
 * Allows other tasks to run.
 */
void rt_task_yield(void) {
    /*
     * Cooperative yield point. Generated state machines call this right
     * before saving their state and RETURNING to whatever driver loop is
     * running them (rt_task_join, rt_scheduler_run, or rt_scheduler_run's
     * push-back path). EVERY driver re-queues a popped task whose resume
     * returned incomplete, so the task is always rescheduled by its driver.
     *
     * This function must therefore NOT push the current task itself: doing
     * so double-queues the task (once here, once by the driver). The second
     * queue entry outlives the task — after the awaiter joins, takes the
     * result, and destroys the handle, the stale entry is popped and its
     * freed memory is interpreted as a TaskHandle, calling a garbage
     * resume_fn (observed as SIGBUS in nested-async programs on macOS).
     * It must not run a nested slice of another task either, for the same
     * reason: the driver loop right above is about to do exactly that.
     */
    (void)current_task;
}

/**
 * Poll a task's completion status.
 *
 * @param handle The task handle
 * @return 1 if complete, 0 if pending
 */
int32_t rt_task_poll(TaskHandle* handle) {
    if (handle == NULL) {
        return 1; // NULL task is "complete"
    }
    return handle->completed;
}

/**
 * Run the scheduler until all tasks complete.
 *
 * This is the main event loop for async programs.
 */
void rt_scheduler_run(void) {
    while (task_queue_head != task_queue_tail) {
        TaskHandle* task = task_queue_pop();
        if (task != NULL && !task->completed) {
            TaskHandle* prev = current_task;
            current_task = task;
            task->resume_fn(task->frame);
            current_task = prev;
            if (!task->completed) {
                task_queue_push(task);
            }
        }
    }
}

/* ============================================================================
 * Debug helper — prints an i64. Used by tests.
 * ============================================================================ */

void rt_print_i64(int64_t value) {
    printf("%lld\n", (long long)value);
}

/**
 * Destroy a string value, freeing only the heap data.
 *
 * StringVal is the value-type representation {data, len} used by
 * rt_str_concat, rt_int_to_string, and similar functions.  Only the
 * `data` pointer is heap-allocated; the struct itself lives on the
 * stack.  Call this when a String value is no longer needed.
 *
 * @param s String value whose data will be freed
 */
void rt_string_destroy(StringVal s) {
    if (s.data != NULL) {
        free(s.data);
    }
}

void rt_io_print_str(void* s_obj) {
    StringVal s = rt_string_obj_value(s_obj);
    if (s.data && s.len > 0) {
        printf("%.*s", (int)s.len, s.data);
    }
}

void rt_io_println_str(void* s) {
    rt_io_print_str(s);
    printf("\n");
}

void rt_io_print_i32(int32_t value) {
    printf("%d", value);
}

void rt_io_println_i32(int32_t value) {
    printf("%d\n", value);
}

// String utilities for Rolang stdlib

int64_t rt_str_len(StringVal s) {
    return s.len;
}

int64_t rt_str_is_empty(StringVal s) {
    return (s.len == 0) ? 1 : 0;
}

int32_t rt_str_compare(StringVal a, StringVal b) {
    int64_t min_len = (a.len < b.len) ? a.len : b.len;
    if (min_len > 0 && a.data && b.data) {
        int cmp = memcmp(a.data, b.data, (size_t)min_len);
        if (cmp != 0) return (cmp < 0) ? -1 : 1;
    }
    if (a.len < b.len) return -1;
    if (a.len > b.len) return 1;
    return 0;
}

int32_t rt_str_contains(StringVal haystack, StringVal needle) {
    if (needle.len == 0) return 1;
    if (needle.len > haystack.len) return 0;
    if (!haystack.data || !needle.data) return 0;
    for (int64_t i = 0; i <= haystack.len - needle.len; i++) {
        if (memcmp(haystack.data + i, needle.data, (size_t)needle.len) == 0)
            return 1;
    }
    return 0;
}

int32_t rt_str_starts_with(StringVal s, StringVal prefix) {
    if (prefix.len > s.len) return 0;
    if (prefix.len == 0) return 1;
    if (!s.data || !prefix.data) return 0;
    return (memcmp(s.data, prefix.data, (size_t)prefix.len) == 0) ? 1 : 0;
}

int32_t rt_str_ends_with(StringVal s, StringVal suffix) {
    if (suffix.len > s.len) return 0;
    if (suffix.len == 0) return 1;
    if (!s.data || !suffix.data) return 0;
    return (memcmp(s.data + s.len - suffix.len, suffix.data, (size_t)suffix.len) == 0) ? 1 : 0;
}

// String construction helpers

StringVal rt_str_concat(StringVal a, StringVal b) {
    int64_t total_len = a.len + b.len;
    if (total_len == 0) return (StringVal){NULL, 0};
    char* buf = (char*)malloc((size_t)total_len + 1);
    if (!buf) return (StringVal){NULL, 0};
    if (a.data && a.len > 0) memcpy(buf, a.data, (size_t)a.len);
    if (b.data && b.len > 0) memcpy(buf + a.len, b.data, (size_t)b.len);
    buf[total_len] = '\0';
    return (StringVal){buf, total_len};
}

StringVal rt_int_to_string(int64_t value) {
    char buf[32];
    int len = snprintf(buf, sizeof(buf), "%lld", (long long)value);
    if (len <= 0) return (StringVal){NULL, 0};
    char* data = (char*)malloc((size_t)len + 1);
    if (!data) return (StringVal){NULL, 0};
    memcpy(data, buf, (size_t)len + 1);
    return (StringVal){data, (int64_t)len};
}

StringVal rt_str_repeat(StringVal s, int32_t count) {
    if (count <= 0 || s.len == 0) return (StringVal){NULL, 0};
    /* Reject pathological inputs that would overflow int64 during the
     * length computation (`s.len * count`). Without this check a caller
     * supplying a large `s.len` and `count` could wrap to a small or
     * negative `total`, the malloc would succeed, and the memcpy loop
     * would write far past the allocated buffer — a classic heap
     * overflow. We also guard against the +1 NUL byte overflowing. */
    if (s.len > (INT64_MAX - 1) / (int64_t)count) {
        rt_panic("rt_str_repeat: result length overflows int64");
    }
    int64_t total = (int64_t)s.len * (int64_t)count;
    char* buf = (char*)malloc((size_t)total + 1);
    if (!buf) return (StringVal){NULL, 0};
    for (int32_t i = 0; i < count; i++) {
        memcpy(buf + (size_t)i * (size_t)s.len, s.data, (size_t)s.len);
    }
    buf[total] = '\0';
    return (StringVal){buf, total};
}

// String inspection helpers

int32_t rt_str_char_at(StringVal s, int32_t index) {
    if (index < 0 || index >= s.len) return -1;
    return (unsigned char)s.data[index];
}

int32_t rt_str_find_char(StringVal s, int32_t ch, int32_t start) {
    if (start < 0) start = 0;
    for (int64_t i = start; i < s.len; i++) {
        if ((unsigned char)s.data[i] == ch) return (int32_t)i;
    }
    return -1;
}

StringVal rt_str_substring(StringVal s, int32_t start, int32_t length) {
    if (start < 0) start = 0;
    if (start >= s.len || length <= 0) return (StringVal){NULL, 0};
    /* Promote to int64 so ``start + length`` cannot wrap around when both
     * are near INT32_MAX. The old int32 comparison silently took the
     * "fits" branch on overflow and memcpy then read far past the buffer. */
    int64_t end = (int64_t)start + (int64_t)length;
    int64_t cap = (end <= s.len) ? (int64_t)length : (s.len - (int64_t)start);
    if (cap <= 0) return (StringVal){NULL, 0};
    int32_t actual_len = (cap > INT32_MAX) ? INT32_MAX : (int32_t)cap;
    char* buf = (char*)malloc((size_t)actual_len + 1);
    if (!buf) return (StringVal){NULL, 0};
    memcpy(buf, s.data + start, (size_t)actual_len);
    buf[actual_len] = 0;
    return (StringVal){buf, actual_len};
}

int32_t _is_whitespace(char c) {
    return (c == ' ' || c == '\t' || c == '\n' || c == '\r');
}

StringVal rt_str_trim(StringVal s) {
    if (s.len == 0 || !s.data) return (StringVal){NULL, 0};
    int64_t start = 0;
    int64_t end = s.len - 1;
    while (start < s.len && _is_whitespace(s.data[start])) start++;
    while (end >= start && _is_whitespace(s.data[end])) end--;
    if (start > end) return (StringVal){NULL, 0};
    int64_t new_len = end - start + 1;
    char* buf = (char*)malloc((size_t)new_len + 1);
    if (!buf) return (StringVal){NULL, 0};
    memcpy(buf, s.data + start, (size_t)new_len);
    buf[new_len] = 0;
    return (StringVal){buf, new_len};
}

StringVal rt_str_replace(StringVal s, StringVal old, StringVal new_val) {
    if (s.len == 0 || old.len == 0 || old.len > s.len || !s.data || !old.data) {
        /* Return a fresh copy of s so the caller can safely destroy both
         * the input and the return value without double-freeing .data */
        if (s.len == 0 || !s.data) return (StringVal){NULL, 0};
        char* copy = (char*)malloc((size_t)s.len + 1);
        if (!copy) return (StringVal){NULL, 0};
        memcpy(copy, s.data, (size_t)s.len);
        copy[s.len] = '\0';
        return (StringVal){copy, s.len};
    }
    // Count occurrences
    int64_t count = 0;
    for (int64_t i = 0; i <= s.len - old.len; i++) {
        if (memcmp(s.data + i, old.data, (size_t)old.len) == 0) {
            count++;
            i += old.len - 1;
        }
    }
    if (count == 0) {
        /* No replacements — return a fresh copy of s */
        char* copy = (char*)malloc((size_t)s.len + 1);
        if (!copy) return (StringVal){NULL, 0};
        memcpy(copy, s.data, (size_t)s.len);
        copy[s.len] = '\0';
        return (StringVal){copy, s.len};
    }
    int64_t new_len = s.len + count * (new_val.len - old.len);
    char* buf = (char*)malloc((size_t)new_len + 1);
    if (!buf) {
        /* Could not allocate result — return a copy of s instead */
        char* copy = (char*)malloc((size_t)s.len + 1);
        if (!copy) return (StringVal){NULL, 0};
        memcpy(copy, s.data, (size_t)s.len);
        copy[s.len] = '\0';
        return (StringVal){copy, s.len};
    }
    
    char* dst = buf;
    int64_t i = 0;
    while (i < s.len) {
        if (i <= s.len - old.len && memcmp(s.data + i, old.data, (size_t)old.len) == 0) {
            if (new_val.data && new_val.len > 0) {
                memcpy(dst, new_val.data, (size_t)new_val.len);
                dst += new_val.len;
            }
            i += old.len;
        } else {
            *dst++ = s.data[i++];
        }
    }
    *dst = 0;
    return (StringVal){buf, new_len};
}

/**
 * In-place string replacement: modifies s.data, returns updated fat pointer.
 *
 * Unlike rt_str_replace which always allocates a new buffer, this function
 * reuses the original buffer via realloc when the size changes, and returns
 * the (possibly updated) StringVal.  When old_len == new_val_len, replacement
 * is done in-place without any allocation.
 *
 * On allocation failure the original string is returned unchanged.
 */
StringVal rt_str_replace_self(StringVal s, StringVal old, StringVal new_val) {
    if (s.len == 0 || old.len == 0 || old.len > s.len || !s.data || !old.data) {
        return s;
    }

    /* Count occurrences */
    int64_t count = 0;
    int64_t old_len = old.len;
    for (int64_t i = 0; i <= s.len - old_len; i++) {
        if (memcmp(s.data + i, old.data, (size_t)old_len) == 0) {
            count++;
            i += old_len - 1;
        }
    }

    if (count == 0) {
        return s;  /* No matches — return self unchanged */
    }

    int64_t new_len = s.len + count * (new_val.len - old_len);

    if (old_len == new_val.len) {
        /* Same-size replacement: pure in-place, no allocation */
        int64_t i = 0;
        while (i < s.len) {
            if (i <= s.len - old_len
                && memcmp(s.data + i, old.data, (size_t)old_len) == 0) {
                if (new_val.data && new_val.len > 0) {
                    memcpy(s.data + i, new_val.data, (size_t)new_val.len);
                }
                i += old_len;
            } else {
                i++;
            }
        }
        s.len = new_len;
        return s;
    }

    /* Size changes — build result in temp buffer, then realloc original
     * and copy back.  This avoids the complexity of right-to-left in-place
     * replacement when the result is larger. */
    char* buf = (char*)malloc((size_t)new_len + 1);
    if (!buf) {
        return s;  /* Allocation failed, return unchanged */
    }

    char* dst = buf;
    int64_t i = 0;
    while (i < s.len) {
        if (i <= s.len - old_len
            && memcmp(s.data + i, old.data, (size_t)old_len) == 0) {
            if (new_val.data && new_val.len > 0) {
                memcpy(dst, new_val.data, (size_t)new_val.len);
                dst += new_val.len;
            }
            i += old_len;
        } else {
            *dst++ = s.data[i++];
        }
    }
    *dst = '\0';

    /* Realloc original to the new size and copy result back.  If realloc
     * fails, the original allocation is untouched — return it unchanged. */
    char* new_data = (char*)realloc(s.data, (size_t)new_len + 1);
    if (new_data) {
        s.data = new_data;
        memcpy(s.data, buf, (size_t)new_len + 1);
    }
    free(buf);

    s.len = new_len;
    return s;
}

/* ============================================================================
 * Character classification
 * ============================================================================ */

int32_t rt_char_is_digit(int32_t ch) {
    return (ch >= '0' && ch <= '9') ? 1 : 0;
}

int32_t rt_char_is_alpha(int32_t ch) {
    return ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')) ? 1 : 0;
}

int32_t rt_char_is_alnum(int32_t ch) {
    return ((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')) ? 1 : 0;
}

int32_t rt_char_is_space(int32_t ch) {
    return (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r') ? 1 : 0;
}

/* ============================================================================
 * String parsing
 * ============================================================================ */

int64_t rt_str_to_i64(StringVal s) {
    if (s.len == 0 || !s.data) return 0;
    int64_t sign = 1;
    int64_t i = 0;
    if (s.data[0] == '-') { sign = -1; i = 1; }
    else if (s.data[0] == '+') { i = 1; }
    int64_t result = 0;
    for (; i < s.len; i++) {
        char c = s.data[i];
        if (c < '0' || c > '9') break;
        result = result * 10 + (c - '0');
    }
    return result * sign;
}

int32_t rt_str_to_i32(StringVal s) {
    return (int32_t)rt_str_to_i64(s);
}

/* Split `s` on every occurrence of `sep`. Returns a gvec of StringVal,
 * each entry freshly heap-allocated.  Empty `sep` returns NULL.
 *
 * Used by std/string.rl `String.split` extension method.
 */
void* rt_str_split(StringVal s, StringVal sep) {
    if (sep.len == 0 || !sep.data) return NULL;
    void* vec = rt_gvec_new(8, (int32_t)sizeof(StringVal), 0);
    if (!vec) return NULL;
    if (s.len == 0 || !s.data) return vec;
    int64_t start = 0;
    int64_t i = 0;
    while (i <= s.len - sep.len) {
        if (memcmp(s.data + i, sep.data, (size_t)sep.len) == 0) {
            int64_t piece_len = i - start;
            char* buf = (char*)malloc((size_t)piece_len + 1);
            if (buf) {
                if (piece_len > 0) memcpy(buf, s.data + start, (size_t)piece_len);
                buf[piece_len] = '\0';
                StringVal entry = {buf, piece_len};
                vec = rt_gvec_push(vec, &entry);
            }
            i += sep.len;
            start = i;
        } else {
            i++;
        }
    }
    /* Tail piece (everything from `start` to end, even if empty). */
    int64_t tail_len = s.len - start;
    char* buf = (char*)malloc((size_t)tail_len + 1);
    if (buf) {
        if (tail_len > 0) memcpy(buf, s.data + start, (size_t)tail_len);
        buf[tail_len] = '\0';
        StringVal entry = {buf, tail_len};
        vec = rt_gvec_push(vec, &entry);
    }
    return vec;
}

/* Split on '\n'. Trailing '\r' on each line (Windows line endings) is
 * stripped. The final empty line at EOF is dropped, matching what
 * `for line in file.read().lines()` should do.
 */
void* rt_str_lines(StringVal s) {
    void* vec = rt_gvec_new(8, (int32_t)sizeof(StringVal), 0);
    if (!vec) return NULL;
    if (s.len == 0 || !s.data) return vec;
    int64_t start = 0;
    for (int64_t i = 0; i < s.len; i++) {
        if (s.data[i] == '\n') {
            int64_t end = i;
            if (end > start && s.data[end - 1] == '\r') end--;
            int64_t piece_len = end - start;
            char* buf = (char*)malloc((size_t)piece_len + 1);
            if (buf) {
                if (piece_len > 0) memcpy(buf, s.data + start, (size_t)piece_len);
                buf[piece_len] = '\0';
                StringVal entry = {buf, piece_len};
                vec = rt_gvec_push(vec, &entry);
            }
            start = i + 1;
        }
    }
    if (start < s.len) {
        int64_t piece_len = s.len - start;
        char* buf = (char*)malloc((size_t)piece_len + 1);
        if (buf) {
            memcpy(buf, s.data + start, (size_t)piece_len);
            buf[piece_len] = '\0';
            StringVal entry = {buf, piece_len};
            vec = rt_gvec_push(vec, &entry);
        }
    }
    return vec;
}

/* f64 / Bool parsing helpers. */
double rt_str_to_f64(StringVal s) {
    if (s.len == 0 || !s.data) return 0.0;
    /* Defensive: ensure null-termination by copying into a local buffer. */
    char stackbuf[64];
    char* c = stackbuf;
    if ((size_t)s.len + 1 > sizeof(stackbuf)) {
        c = (char*)malloc((size_t)s.len + 1);
        if (!c) return 0.0;
    }
    memcpy(c, s.data, (size_t)s.len);
    c[s.len] = '\0';
    double v = strtod(c, NULL);
    if (c != stackbuf) free(c);
    return v;
}

/* Convert f64 -> StringVal (uses %g). Caller owns the data. */
StringVal rt_f64_to_string(double val) {
    StringVal sv = {NULL, 0};
    char buf[32];
    int n = snprintf(buf, sizeof(buf), "%g", val);
    if (n < 0) return sv;
    char* data = (char*)malloc((size_t)n + 1);
    if (!data) return sv;
    memcpy(data, buf, (size_t)n + 1);
    sv.data = data;
    sv.len = (int64_t)n;
    return sv;
}

/* -------------------------------------------------------------------------
 * Heap String object bridge.
 * ------------------------------------------------------------------------- */

static void* rt_string_handle_from_value(StringVal s) {
    StringVal* handle = (StringVal*)malloc(sizeof(StringVal));
    if (handle == NULL) {
        rt_string_destroy(s);
        return NULL;
    }
    *handle = s;
    return handle;
}

void* rt_string_from_rodata(const char* data, int64_t len) {
    if (len <= 0) {
        return rt_string_handle_from_value((StringVal){NULL, 0});
    }
    char* copy = (char*)malloc((size_t)len + 1);
    if (copy == NULL) {
        return rt_string_handle_from_value((StringVal){NULL, 0});
    }
    if (data != NULL) memcpy(copy, data, (size_t)len);
    copy[len] = '\0';
    return rt_string_handle_from_value((StringVal){copy, len});
}

void rt_string_free_data(void* data) {
    if (data != NULL) free(data);
}

void rt_string_free_handle(void* handle) {
    if (handle == NULL) return;
    StringVal s = *(StringVal*)handle;
    rt_string_destroy(s);
    free(handle);
}

char* rt_string_handle_data(void* handle) {
    if (handle == NULL) return NULL;
    return ((StringVal*)handle)->data;
}

int64_t rt_string_handle_len(void* handle) {
    if (handle == NULL) return 0;
    return ((StringVal*)handle)->len;
}

void rt_string_free_handle_only(void* handle) {
    free(handle);
}

void rt_string_release(void* s) {
    if (s != NULL) rt_obj_release(s);
}

int64_t rt_string_len(void* s) { return rt_str_len(rt_string_obj_value(s)); }
int64_t rt_string_is_empty(void* s) { return rt_str_is_empty(rt_string_obj_value(s)); }
int32_t rt_string_compare(void* a, void* b) { return rt_str_compare(rt_string_obj_value(a), rt_string_obj_value(b)); }
int32_t rt_string_contains(void* h, void* n) { return rt_str_contains(rt_string_obj_value(h), rt_string_obj_value(n)); }
int32_t rt_string_starts_with(void* s, void* p) { return rt_str_starts_with(rt_string_obj_value(s), rt_string_obj_value(p)); }
int32_t rt_string_ends_with(void* s, void* suffix) { return rt_str_ends_with(rt_string_obj_value(s), rt_string_obj_value(suffix)); }
void* rt_string_concat_handle(void* a, void* b) { return rt_string_handle_from_value(rt_str_concat(rt_string_obj_value(a), rt_string_obj_value(b))); }
void* rt_int_to_string_handle(int64_t value) { return rt_string_handle_from_value(rt_int_to_string(value)); }
void* rt_f64_to_string_handle(double value) { return rt_string_handle_from_value(rt_f64_to_string(value)); }
void* rt_string_repeat_handle(void* s, int32_t count) { return rt_string_handle_from_value(rt_str_repeat(rt_string_obj_value(s), count)); }
int32_t rt_string_char_at(void* s, int32_t index) { return rt_str_char_at(rt_string_obj_value(s), index); }
int32_t rt_string_find_char(void* s, int32_t ch, int32_t start) { return rt_str_find_char(rt_string_obj_value(s), ch, start); }
void* rt_string_substring_handle(void* s, int32_t start, int32_t len) { return rt_string_handle_from_value(rt_str_substring(rt_string_obj_value(s), start, len)); }
void* rt_string_trim_handle(void* s) { return rt_string_handle_from_value(rt_str_trim(rt_string_obj_value(s))); }
void* rt_string_replace_handle(void* s, void* old, void* new_val) {
    return rt_string_handle_from_value(rt_str_replace(
        rt_string_obj_value(s),
        rt_string_obj_value(old),
        rt_string_obj_value(new_val)
    ));
}
int64_t rt_string_to_i64(void* s) { return rt_str_to_i64(rt_string_obj_value(s)); }
int32_t rt_string_to_i32(void* s) { return rt_str_to_i32(rt_string_obj_value(s)); }
double rt_string_to_f64(void* s) { return rt_str_to_f64(rt_string_obj_value(s)); }

// ============================================================================
// Generic dynamic vectors (for generic Vec<T>)
// Layout: { int32_t len, int32_t capacity, int32_t elem_size,
//           int32_t elem_type_id, data... }
//
// elem_type_id is the type descriptor index for the element type.
// 0 = primitive value type (i32, i64, f64, etc.) — no retain/release needed.
// Non-zero = heap type (struct, enum, tuple) — retain/release elements on push/pop/set/free.
// ============================================================================

typedef struct {
    int32_t len;
    int32_t capacity;
    int32_t elem_size;
    int32_t elem_type_id;
} GVecHeader;

/** Retain a heap-typed element stored at *elem_ptr if elem_type_id != 0. */
static inline void _gvec_retain_element(int32_t elem_type_id, const void* elem_ptr) {
    if (elem_type_id == 0 || elem_ptr == NULL) return;
    void* obj = *(void**)elem_ptr;
    if (obj != NULL) {
        rt_obj_retain(obj);
    }
}

/** Release a heap-typed element stored at *elem_ptr if elem_type_id != 0. */
static inline void _gvec_release_element(int32_t elem_type_id, void* elem_ptr) {
    if (elem_type_id == 0 || elem_ptr == NULL) return;
    void* obj = *(void**)elem_ptr;
    if (obj != NULL) {
        rt_obj_release(obj);
    }
}

/** Release all heap-typed elements in a vec, then free the vec. */
static void _gvec_release_all(GVecHeader* h) {
    if (h->elem_type_id == 0) return;
    unsigned char* data = (unsigned char*)(h + 1);
    for (int32_t i = 0; i < h->len; i++) {
        void* obj = *(void**)(data + (size_t)i * (size_t)h->elem_size);
        if (obj != NULL) {
            rt_obj_release(obj);
        }
    }
}

/*
 * GC trace hook for ``Vec<T>``. Codegen installs this on every
 * monomorphized ``Vec_*`` type's ``TypeDescriptor.trace_fn`` so the
 * cycle collector can reach into the runtime-allocated buffer that
 * the ``handle: RawPtr`` field points at.
 *
 * ``payload`` is the Rolang struct payload — by layout the first 8
 * bytes are the ``handle`` pointer (the rest are primitive fields
 * irrelevant to the GC).
 */
void rt_gvec_gc_trace(void* payload, GCTraceCb cb, void* ctx) {
    if (payload == NULL || cb == NULL) return;
    void* handle = *(void**)payload;
    if (handle == NULL) return;

    GVecHeader* h = (GVecHeader*)handle;
    /* elem_type_id == 0 means the buffer holds primitive bytes — no
     * managed pointers to trace. */
    if (h->elem_type_id == 0) return;
    if (h->len <= 0 || h->elem_size <= 0) return;

    unsigned char* data = (unsigned char*)(h + 1);
    for (int32_t i = 0; i < h->len; i++) {
        void* slot = *(void**)(data + (size_t)i * (size_t)h->elem_size);
        if (slot != NULL) {
            cb(slot, ctx);
        }
    }
}

void* rt_gvec_new(int32_t capacity, int32_t elem_size, int32_t elem_type_id) {
    if (capacity < 4) capacity = 4;
    if (elem_size <= 0) elem_size = 1;
    size_t total = sizeof(GVecHeader) + (size_t)capacity * (size_t)elem_size;
    GVecHeader* h = (GVecHeader*)malloc(total);
    if (!h) return NULL;
    h->len = 0;
    h->capacity = capacity;
    h->elem_size = elem_size;
    h->elem_type_id = elem_type_id;
    return h;
}

int32_t rt_gvec_len(void* vec) {
    if (!vec) return 0;
    return ((GVecHeader*)vec)->len;
}

int32_t rt_gvec_capacity(void* vec) {
    if (!vec) return 0;
    return ((GVecHeader*)vec)->capacity;
}

int32_t rt_gvec_elem_size(void* vec) {
    if (!vec) return 0;
    return ((GVecHeader*)vec)->elem_size;
}

void rt_gvec_get(void* vec, int32_t index, void* out) {
    if (!vec || !out) rt_panic("gvec_get on null vec or out pointer");
    GVecHeader* h = (GVecHeader*)vec;
    if (index < 0 || index >= h->len) {
        rt_panic_index_out_of_bounds((int64_t)index, (int64_t)h->len);
    }
    unsigned char* data = (unsigned char*)(h + 1);
    void* slot = data + (size_t)index * (size_t)h->elem_size;
    _rt_copy_small(out, slot, (size_t)h->elem_size);
    /* Retain heap-typed elements for the caller. The vec still owns the
     * slot, so the caller's `out` becomes a fresh strong reference. Without
     * this, dropping `out` would call rt_obj_release on a slot the vec
     * still references — UAF on the next vec destruction. */
    _gvec_retain_element(h->elem_type_id, out);
}

void rt_gvec_set(void* vec, int32_t index, const void* value) {
    if (!vec || !value) rt_panic("gvec_set on null vec or value");
    GVecHeader* h = (GVecHeader*)vec;
    if (index < 0 || index >= h->len) {
        rt_panic_index_out_of_bounds((int64_t)index, (int64_t)h->len);
    }

    /* Release old element if heap-typed */
    unsigned char* data = (unsigned char*)(h + 1);
    void* slot = data + (size_t)index * (size_t)h->elem_size;
    _gvec_release_element(h->elem_type_id, slot);

    /* Copy new value */
    memcpy(slot, value, (size_t)h->elem_size);

    /* Retain new element if heap-typed */
    _gvec_retain_element(h->elem_type_id, slot);
}

void* rt_gvec_resize(void* vec, int32_t new_capacity) {
    if (!vec || new_capacity <= 0) return vec;
    GVecHeader* h = (GVecHeader*)vec;
    if (new_capacity <= h->capacity) return vec;
    size_t elem_size = (size_t)h->elem_size;
    size_t new_data_size = (size_t)new_capacity * elem_size;
    size_t total = sizeof(GVecHeader) + new_data_size;
    GVecHeader* new_h = (GVecHeader*)realloc(h, total);
    if (!new_h) return vec;
    new_h->capacity = new_capacity;
    return new_h;
}

void* rt_gvec_push(void* vec, const void* value) {
    if (!vec || !value) return vec;
    GVecHeader* h = (GVecHeader*)vec;
    if (h->len >= h->capacity) {
        /* ``h->capacity * 2`` is signed int32 multiplication and wraps to
         * a negative value when capacity exceeds INT32_MAX/2. Detect
         * saturation explicitly and panic — exceeding 2^31 elements in a
         * single Vec is a real bug
         * either way. */
        int32_t cap = h->capacity;
        int32_t new_cap;
        if (cap >= (INT32_MAX / 2)) {
            if (cap >= INT32_MAX) {
                rt_panic("Vec capacity exceeds INT32_MAX");
            }
            new_cap = INT32_MAX;
        } else {
            new_cap = cap * 2;
        }
        if (new_cap < 8) new_cap = 8;
        vec = rt_gvec_resize(vec, new_cap);
        h = (GVecHeader*)vec;
        if (h->capacity <= h->len) {
            rt_panic("rt_gvec_push: resize failed to grow Vec capacity");
        }
    }
    unsigned char* data = (unsigned char*)(h + 1);
    void* slot = data + (size_t)h->len * (size_t)h->elem_size;
    memcpy(slot, value, (size_t)h->elem_size);
    _gvec_retain_element(h->elem_type_id, slot);
    h->len++;
    return vec;
}

void rt_gvec_pop(void* vec, void* out) {
    if (!vec || !out) return;
    GVecHeader* h = (GVecHeader*)vec;
    if (h->len <= 0) {
        /* Empty pop: zero the caller's slot. */
        unsigned char* eh = (unsigned char*)h;
        (void)eh;
        memset(out, 0, (size_t)h->elem_size);
        return;
    }
    h->len--;
    unsigned char* data = (unsigned char*)(h + 1);
    void* slot = data + (size_t)h->len * (size_t)h->elem_size;
    memcpy(out, slot, (size_t)h->elem_size);
    /* Ownership transfer: the slot's strong reference moves to the caller
     * via `out`. Do NOT release the slot here — doing so would leave the
     * caller with a dangling pointer in the common case where the vec was
     * the only owner. We do, however, zero the now-vacated slot so a later
     * push doesn't reuse a stale pointer that ARC might confuse for a
     * still-live reference. */
    memset(slot, 0, (size_t)h->elem_size);
}

void rt_gvec_free(void* vec) {
    if (!vec) return;
    GVecHeader* h = (GVecHeader*)vec;
    _gvec_release_all(h);
    free(vec);
}

/* ============================================================================
 * File I/O
 * ============================================================================ */

#include <sys/stat.h>

void* rt_file_open(const char* path, const char* mode) {
    if (!path || !mode) return NULL;
    return (void*)fopen(path, mode);
}

void rt_file_close(void* file) {
    if (file) fclose((FILE*)file);
}

int32_t rt_file_read(void* file, void* buf, int32_t size) {
    if (!file || !buf || size <= 0) return 0;
    size_t n = fread(buf, 1, (size_t)size, (FILE*)file);
    return (int32_t)n;
}

int32_t rt_file_write(void* file, const void* buf, int32_t size) {
    if (!file || !buf || size <= 0) return 0;
    size_t n = fwrite(buf, 1, (size_t)size, (FILE*)file);
    return (int32_t)n;
}

int32_t rt_file_seek(void* file, int64_t offset, int32_t whence) {
    if (!file) return -1;
    return fseek((FILE*)file, (long)offset, (int)whence);
}

int64_t rt_file_tell(void* file) {
    if (!file) return -1;
    return (int64_t)ftell((FILE*)file);
}

int32_t rt_file_flush(void* file) {
    if (!file) return -1;
    return fflush((FILE*)file);
}

int32_t rt_file_eof(void* file) {
    if (!file) return 1;
    return feof((FILE*)file);
}

void* rt_file_read_all(void* file) {
    if (!file) return NULL;
    FILE* f = (FILE*)file;
    long pos = ftell(f);
    if (pos < 0) return NULL;
    fseek(f, 0, SEEK_END);
    long end = ftell(f);
    fseek(f, pos, SEEK_SET);
    if (end < 0 || end < pos) return NULL;
    /* `end` is the absolute end-of-file offset; we need the number of
     * remaining bytes from `pos`. */
    long remaining = end - pos;
    char* buf = (char*)malloc((size_t)remaining + 1);
    if (!buf) return NULL;
    long n = (long)fread(buf, 1, (size_t)remaining, f);
    if (n < 0) n = 0;
    buf[n] = '\0';
    return (void*)buf;
}

StringVal rt_file_read_all_s(void* file) {
    StringVal sv = {NULL, 0};
    char* data = (char*)rt_file_read_all(file);
    if (!data) return sv;
    sv.data = data;
    sv.len = (int64_t)strlen(data);
    return sv;
}

void* rt_file_read_line(void* file) {
    if (!file) return NULL;
    FILE* f = (FILE*)file;
    size_t cap = 128;
    char* buf = (char*)malloc(cap);
    if (!buf) return NULL;
    size_t pos = 0;
    while (1) {
        int c = fgetc(f);
        if (c == EOF) {
            if (pos == 0) { free(buf); return NULL; }
            break;
        }
        buf[pos++] = (char)c;
        if (pos >= cap) {
            cap *= 2;
            char* new_buf = (char*)realloc(buf, cap);
            if (!new_buf) { free(buf); return NULL; }
            buf = new_buf;
        }
        if (c == '\n') break;
    }
    buf[pos] = '\0';
    return (void*)buf;
}

StringVal rt_file_read_line_s(void* file) {
    StringVal sv = {NULL, 0};
    char* data = (char*)rt_file_read_line(file);
    if (!data) return sv;
    sv.data = data;
    sv.len = (int64_t)strlen(data);
    return sv;
}

int32_t rt_file_write_str(void* file, const void* str) {
    if (!file || !str) return 0;
    size_t len = strlen((const char*)str);
    return rt_file_write(file, str, (int32_t)len);
}

int64_t rt_file_get_size(const char* path) {
    if (!path) return 0;
    struct stat st;
    if (stat(path, &st) != 0) return 0;
    return (int64_t)st.st_size;
}

// String-based wrappers for Rolang StringVal (fat pointer { data, len })
void* rt_file_open_s(StringVal path, StringVal mode) {
    return rt_file_open(path.data, mode.data);
}
int32_t rt_file_write_s(void* file, StringVal s) {
    return rt_file_write(file, s.data, (int32_t)s.len);
}
int64_t rt_file_get_size_s(StringVal path) {
    return rt_file_get_size(path.data);
}

void* rt_file_open_string(void* path, void* mode) {
    StringVal p = rt_string_obj_value(path);
    StringVal m = rt_string_obj_value(mode);
    return rt_file_open(p.data, m.data);
}

/* Open a file from a String path object using an integer mode:
 *   0 = read ("rb"), 1 = write/truncate ("wb"), 2 = append ("ab").
 * Binary modes are used so rt_file_read/rt_file_write byte counts are exact
 * on every platform. Returns a FILE* (as void*) or NULL on failure. */
void* rt_file_open_handle(void* path, int32_t mode) {
    StringVal p = rt_string_obj_value(path);
    if (p.data == NULL) return NULL;
    const char* m;
    switch (mode) {
        case 1:  m = "wb"; break;
        case 2:  m = "ab"; break;
        case 0:
        default: m = "rb"; break;
    }
    return rt_file_open(p.data, m);
}

int32_t rt_file_write_string(void* file, void* s_obj) {
    StringVal s = rt_string_obj_value(s_obj);
    return rt_file_write(file, s.data, (int32_t)s.len);
}

int64_t rt_file_get_size_string(void* path) {
    StringVal p = rt_string_obj_value(path);
    return rt_file_get_size(p.data);
}

void* rt_file_read_all_handle(void* file) {
    return rt_string_handle_from_value(rt_file_read_all_s(file));
}

void* rt_file_read_line_handle(void* file) {
    return rt_string_handle_from_value(rt_file_read_line_s(file));
}

/* ============================================================================
 * String Formatting
 * ============================================================================ */

// Replace first {} in fmt with the integer value, returns malloc'd string
void* rt_format_int(const char* fmt, int32_t val) {
    if (!fmt) return NULL;
    char buf[32];
    snprintf(buf, sizeof(buf), "%d", val);
    const char* pos = strstr(fmt, "{}");
    if (!pos) return strdup(fmt);
    size_t prefix_len = (size_t)(pos - fmt);
    size_t total = prefix_len + strlen(buf) + strlen(pos + 2) + 1;
    char* result = (char*)malloc(total);
    if (!result) return NULL;
    memcpy(result, fmt, prefix_len);
    memcpy(result + prefix_len, buf, strlen(buf));
    strcpy(result + prefix_len + strlen(buf), pos + 2);
    return result;
}

// Replace first {} in fmt with the i64 value
void* rt_format_i64(const char* fmt, int64_t val) {
    if (!fmt) return NULL;
    char buf[32];
    snprintf(buf, sizeof(buf), "%lld", (long long)val);
    const char* pos = strstr(fmt, "{}");
    if (!pos) return strdup(fmt);
    size_t prefix_len = (size_t)(pos - fmt);
    size_t total = prefix_len + strlen(buf) + strlen(pos + 2) + 1;
    char* result = (char*)malloc(total);
    if (!result) return NULL;
    memcpy(result, fmt, prefix_len);
    memcpy(result + prefix_len, buf, strlen(buf));
    strcpy(result + prefix_len + strlen(buf), pos + 2);
    return result;
}

// Replace first {} with string value (C string)
void* rt_format_str(const char* fmt, const char* val) {
    if (!fmt) return NULL;
    if (!val) val = "(null)";
    const char* pos = strstr(fmt, "{}");
    if (!pos) return strdup(fmt);
    size_t prefix_len = (size_t)(pos - fmt);
    size_t total = prefix_len + strlen(val) + strlen(pos + 2) + 1;
    char* result = (char*)malloc(total);
    if (!result) return NULL;
    memcpy(result, fmt, prefix_len);
    memcpy(result + prefix_len, val, strlen(val));
    strcpy(result + prefix_len + strlen(val), pos + 2);
    return result;
}

// StringVal wrappers for Rolang
StringVal rt_format_int_s(StringVal fmt, int32_t val) {
    StringVal sv = {NULL, 0};
    char* data = (char*)rt_format_int(fmt.data, val);
    if (!data) return sv;
    sv.data = data;
    sv.len = (int64_t)strlen(data);
    return sv;
}

StringVal rt_format_i64_s(StringVal fmt, int64_t val) {
    StringVal sv = {NULL, 0};
    char* data = (char*)rt_format_i64(fmt.data, val);
    if (!data) return sv;
    sv.data = data;
    sv.len = (int64_t)strlen(data);
    return sv;
}

StringVal rt_format_str_s(StringVal fmt, StringVal val) {
    StringVal sv = {NULL, 0};
    char* data = (char*)rt_format_str(fmt.data, val.data);
    if (!data) return sv;
    sv.data = data;
    sv.len = (int64_t)strlen(data);
    return sv;
}

/* Replace first {} in fmt with the f64 value (via %g formatting). */
StringVal rt_format_f64_s(StringVal fmt, double val) {
    StringVal sv = {NULL, 0};
    if (!fmt.data) return sv;
    char numbuf[32];
    int n = snprintf(numbuf, sizeof(numbuf), "%g", val);
    if (n < 0) return sv;
    /* Find first "{}" in fmt */
    int64_t pos = -1;
    for (int64_t i = 0; i + 1 < fmt.len; i++) {
        if (fmt.data[i] == '{' && fmt.data[i + 1] == '}') { pos = i; break; }
    }
    int64_t total;
    char* result;
    if (pos < 0) {
        /* No placeholder — return a copy of fmt */
        result = (char*)malloc((size_t)fmt.len + 1);
        if (!result) return sv;
        memcpy(result, fmt.data, (size_t)fmt.len);
        result[fmt.len] = '\0';
        sv.data = result;
        sv.len = fmt.len;
        return sv;
    }
    total = pos + (int64_t)n + (fmt.len - pos - 2);
    result = (char*)malloc((size_t)total + 1);
    if (!result) return sv;
    memcpy(result, fmt.data, (size_t)pos);
    memcpy(result + pos, numbuf, (size_t)n);
    memcpy(result + pos + n, fmt.data + pos + 2, (size_t)(fmt.len - pos - 2));
    result[total] = '\0';
    sv.data = result;
    sv.len = total;
    return sv;
}

/* Replace first {} in fmt with "true" or "false". */
StringVal rt_format_bool_s(StringVal fmt, int32_t val) {
    StringVal word;
    word.data = (val != 0) ? "true" : "false";
    word.len = (val != 0) ? 4 : 5;
    return rt_format_str_s(fmt, word);
}

void* rt_format_int_handle(void* fmt, int32_t val) {
    return rt_string_handle_from_value(rt_format_int_s(rt_string_obj_value(fmt), val));
}

void* rt_format_i64_handle(void* fmt, int64_t val) {
    return rt_string_handle_from_value(rt_format_i64_s(rt_string_obj_value(fmt), val));
}

void* rt_format_f64_handle(void* fmt, double val) {
    return rt_string_handle_from_value(rt_format_f64_s(rt_string_obj_value(fmt), val));
}

void* rt_format_bool_handle(void* fmt, int32_t val) {
    return rt_string_handle_from_value(rt_format_bool_s(rt_string_obj_value(fmt), val));
}

void* rt_format_str_handle(void* fmt, void* val) {
    return rt_string_handle_from_value(rt_format_str_s(
        rt_string_obj_value(fmt),
        rt_string_obj_value(val)
    ));
}

/* ============================================================================
 * Multi-argument formatting
 *
 * `rt_fmt_args` walks `fmt` looking for `{}` placeholders. Each placeholder
 * is filled by the next entry of the parallel `argv` / `kinds` arrays:
 *
 *     kinds[i] = 0  : argv[i].i is the integer value (i64)
 *     kinds[i] = 1  : argv[i].s is the StringVal value
 *     kinds[i] = 2  : argv[i].b is the Bool (0/1) value
 *     kinds[i] = 3  : argv[i].f is the f64 value
 *
 * Extra placeholders past `nargs` are kept as literal "{}".
 * Extra args past the last placeholder are silently dropped.
 *
 * The result is a freshly heap-allocated StringVal — caller owns the data.
 * ============================================================================ */

typedef union {
    int64_t   i;
    double    f;
    int32_t   b;
    StringVal s;
} FmtArg;

StringVal rt_fmt_args(StringVal fmt, int32_t nargs, const int32_t* kinds, const FmtArg* argv) {
    StringVal out = {NULL, 0};
    if (fmt.len == 0 || !fmt.data) {
        return out;
    }

    /* Estimate capacity: format length + 32 bytes per arg (i64 max ~20 chars,
     * doubles ~24 chars, bools 5). Strings need exact size. */
    size_t cap = (size_t)fmt.len + 1;
    for (int32_t i = 0; i < nargs; i++) {
        switch (kinds ? kinds[i] : -1) {
            case 1: cap += (size_t)argv[i].s.len; break;
            case 3: cap += 32; break;
            case 2: cap += 5; break;
            default: cap += 24; break;
        }
    }
    char* buf = (char*)malloc(cap + 1);
    if (!buf) return out;

    size_t bp = 0;
    int32_t arg_idx = 0;
    int64_t i = 0;
    while (i < fmt.len) {
        if (i + 1 < fmt.len && fmt.data[i] == '{' && fmt.data[i + 1] == '}') {
            if (kinds && argv && arg_idx < nargs) {
                char numbuf[32];
                int numlen = 0;
                switch (kinds[arg_idx]) {
                    case 0:
                        numlen = snprintf(numbuf, sizeof(numbuf), "%lld",
                                          (long long)argv[arg_idx].i);
                        if (numlen < 0) numlen = 0;
                        if ((size_t)numlen > cap - bp) numlen = (int)(cap - bp);
                        memcpy(buf + bp, numbuf, (size_t)numlen);
                        bp += (size_t)numlen;
                        break;
                    case 1: {
                        StringVal s = argv[arg_idx].s;
                        if (s.data && s.len > 0) {
                            size_t need = (size_t)s.len;
                            if (bp + need >= cap) {
                                size_t new_cap = (bp + need + 1) * 2;
                                char* nb = (char*)realloc(buf, new_cap + 1);
                                if (!nb) { free(buf); return out; }
                                buf = nb; cap = new_cap;
                            }
                            memcpy(buf + bp, s.data, need);
                            bp += need;
                        }
                        break;
                    }
                    case 2: {
                        const char* w = argv[arg_idx].b ? "true" : "false";
                        size_t need = strlen(w);
                        if (bp + need >= cap) {
                            size_t new_cap = (bp + need + 1) * 2;
                            char* nb = (char*)realloc(buf, new_cap + 1);
                            if (!nb) { free(buf); return out; }
                            buf = nb; cap = new_cap;
                        }
                        memcpy(buf + bp, w, need);
                        bp += need;
                        break;
                    }
                    case 3:
                        numlen = snprintf(numbuf, sizeof(numbuf), "%g", argv[arg_idx].f);
                        if (numlen < 0) numlen = 0;
                        if (bp + (size_t)numlen >= cap) {
                            size_t new_cap = (bp + (size_t)numlen + 1) * 2;
                            char* nb = (char*)realloc(buf, new_cap + 1);
                            if (!nb) { free(buf); return out; }
                            buf = nb; cap = new_cap;
                        }
                        memcpy(buf + bp, numbuf, (size_t)numlen);
                        bp += (size_t)numlen;
                        break;
                    default:
                        if (bp + 2 < cap) { buf[bp++] = '{'; buf[bp++] = '}'; }
                        break;
                }
                arg_idx++;
            } else {
                if (bp + 2 < cap) { buf[bp++] = '{'; buf[bp++] = '}'; }
            }
            i += 2;
        } else {
            if (bp + 1 >= cap) {
                size_t new_cap = (cap + 1) * 2;
                char* nb = (char*)realloc(buf, new_cap + 1);
                if (!nb) { free(buf); return out; }
                buf = nb; cap = new_cap;
            }
            buf[bp++] = fmt.data[i++];
        }
    }
    buf[bp] = '\0';
    out.data = buf;
    out.len = (int64_t)bp;
    return out;
}

/* ============================================================================
 * Process / environment / argv / exit / stdin / panic-msg
 *
 * These are the externs `process.rl` and `panic.rl` build on. None of them
 * were available before — they unblock self-host work.
 *
 * Argv handling: the runtime supplies the actual `int main(int argc, char**)`,
 * stashes argc/argv into globals, and dispatches to the user-renamed entry
 * point `__rolang_user_main` (see codegen's `_rename_user_main`). User code
 * reads argc/argv via `rt_args_count` and `rt_args_get`.
 * ============================================================================ */

#include <errno.h>
#if defined(__linux__) || defined(__APPLE__)
#  include <sys/types.h>
#  include <sys/wait.h>
#  include <dirent.h>
#  include <libgen.h>
#endif

/* Argv globals — populated by rt_main_wrapper at process start. */
static int      rt_argc_global = 0;
static char**   rt_argv_global = NULL;

int32_t rt_args_count(void) {
    return (int32_t)rt_argc_global;
}

StringVal rt_args_get(int32_t index) {
    StringVal out = {NULL, 0};
    if (index < 0 || index >= rt_argc_global || rt_argv_global == NULL) return out;
    const char* s = rt_argv_global[index];
    if (!s) return out;
    int64_t len = (int64_t)strlen(s);
    char* buf = (char*)malloc((size_t)len + 1);
    if (!buf) return out;
    memcpy(buf, s, (size_t)len);
    buf[len] = '\0';
    out.data = buf;
    out.len = len;
    return out;
}

StringVal rt_env_get(StringVal name) {
    StringVal out = {NULL, 0};
    if (!name.data || name.len <= 0) return out;
    /* getenv expects a C string; name.data is null-terminated by our string
     * constructors but we copy defensively in case it isn't. */
    char stackbuf[256];
    char* cname = stackbuf;
    if ((size_t)name.len + 1 > sizeof(stackbuf)) {
        cname = (char*)malloc((size_t)name.len + 1);
        if (!cname) return out;
    }
    memcpy(cname, name.data, (size_t)name.len);
    cname[name.len] = '\0';

    const char* val = getenv(cname);
    if (cname != stackbuf) free(cname);
    if (!val) return out;

    int64_t len = (int64_t)strlen(val);
    char* buf = (char*)malloc((size_t)len + 1);
    if (!buf) return out;
    memcpy(buf, val, (size_t)len);
    buf[len] = '\0';
    out.data = buf;
    out.len = len;
    return out;
}

int32_t rt_env_set(StringVal name, StringVal value) {
#if defined(__linux__) || defined(__APPLE__)
    if (!name.data || name.len <= 0) return -1;
    char nbuf[256], vbuf[1024];
    char* cname = nbuf;
    char* cval = vbuf;
    if ((size_t)name.len + 1 > sizeof(nbuf)) {
        cname = (char*)malloc((size_t)name.len + 1);
        if (!cname) return -1;
    }
    memcpy(cname, name.data, (size_t)name.len);
    cname[name.len] = '\0';

    if (!value.data || value.len == 0) {
        if (cname != nbuf) { int r = unsetenv(cname); free(cname); return r; }
        return unsetenv(cname);
    }
    if ((size_t)value.len + 1 > sizeof(vbuf)) {
        cval = (char*)malloc((size_t)value.len + 1);
        if (!cval) { if (cname != nbuf) free(cname); return -1; }
    }
    memcpy(cval, value.data, (size_t)value.len);
    cval[value.len] = '\0';

    int rc = setenv(cname, cval, 1);
    if (cname != nbuf) free(cname);
    if (cval != vbuf) free(cval);
    return rc;
#else
    (void)name; (void)value;
    return -1;
#endif
}

/* Run a shell command. Returns the child exit code or -1 on launch failure. */
int32_t rt_process_system(StringVal cmd) {
    if (!cmd.data || cmd.len <= 0) return -1;
    /* Defensive nul-terminate copy. */
    char stackbuf[1024];
    char* c = stackbuf;
    if ((size_t)cmd.len + 1 > sizeof(stackbuf)) {
        c = (char*)malloc((size_t)cmd.len + 1);
        if (!c) return -1;
    }
    memcpy(c, cmd.data, (size_t)cmd.len);
    c[cmd.len] = '\0';
    int rc = system(c);
    if (c != stackbuf) free(c);
#if defined(__linux__) || defined(__APPLE__)
    if (WIFEXITED(rc)) return WEXITSTATUS(rc);
    return rc;
#else
    return rc;
#endif
}

/* Run an external program by argv vector — no shell, no interpolation,
 * no metacharacter expansion. Safe alternative to `system()` for callers
 * who need to feed untrusted data into a command. The argv RawPtr is a
 * Vec<String>'s handle; we walk it via the gvec ABI.
 *
 * Returns the child's exit code, or -1 on launch failure. */
int32_t rt_process_run_argv(void* argv_vec) {
#if defined(__linux__) || defined(__APPLE__)
    if (argv_vec == NULL) return -1;
    GVecHeader* h = (GVecHeader*)argv_vec;
    if (h->len <= 0) return -1;
    /* Element size must match a String pointer (heap representation). */
    if (h->elem_size != (int32_t)sizeof(void*)) return -1;

    /* Build a NULL-terminated char* array from the Vec<String>. We allocate
     * separately from the strings themselves so we don't have to scribble
     * into the runtime's own buffers. */
    int n = h->len;
    char** argv = (char**)calloc((size_t)n + 1, sizeof(char*));
    if (!argv) return -1;
    unsigned char* data = (unsigned char*)(h + 1);
    int ok = 1;
    for (int i = 0; i < n; i++) {
        void* str_obj = *(void**)(data + (size_t)i * (size_t)h->elem_size);
        if (!str_obj) { ok = 0; break; }
        /* The String's {data, len} live inline in the ARC object payload;
         * use the canonical accessor rather than assuming a separate heap
         * StringVal handle (the layout prior to the inline-payload change). */
        StringVal sv = rt_string_obj_value(str_obj);
        size_t len = (size_t)(sv.len > 0 ? sv.len : 0);
        char* dup = (char*)malloc(len + 1);
        if (!dup) { ok = 0; break; }
        if (len > 0 && sv.data != NULL) memcpy(dup, sv.data, len);
        dup[len] = '\0';
        argv[i] = dup;
    }
    if (!ok) {
        for (int i = 0; i < n; i++) free(argv[i]);
        free(argv);
        return -1;
    }
    argv[n] = NULL;

    pid_t pid = fork();
    if (pid < 0) {
        for (int i = 0; i < n; i++) free(argv[i]);
        free(argv);
        return -1;
    }
    if (pid == 0) {
        /* child: execvp searches PATH but does not invoke a shell. */
        execvp(argv[0], argv);
        /* If execvp returns, it failed. */
        _exit(127);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {
        for (int i = 0; i < n; i++) free(argv[i]);
        free(argv);
        return -1;
    }
    for (int i = 0; i < n; i++) free(argv[i]);
    free(argv);
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return -1;
    return status;
#else
    (void)argv_vec;
    return -1;
#endif
}

__attribute__((noreturn))
void rt_exit(int32_t code) {
    /* Use _exit (POSIX) rather than libc exit() so we don't risk
     * re-entering any Rolang-defined `exit` symbol via the dynamic
     * link table.
     */
    fflush(stdout);
    fflush(stderr);
#if defined(__linux__) || defined(__APPLE__)
    _exit((int)code);
#else
    exit((int)code);
#endif
}

__attribute__((noreturn))
void rt_panic_msg(StringVal msg) {
    fflush(stdout);  /* don't lose output buffered before the panic */
    if (msg.data && msg.len > 0) {
        fprintf(stderr, "rolang panic: %.*s\n", (int)msg.len, msg.data);
    } else {
        fprintf(stderr, "rolang panic: (no message)\n");
    }
    fflush(stderr);
    abort();
}

/* ---- stdin ---- */

StringVal rt_stdin_read_line(void) {
    StringVal out = {NULL, 0};
    size_t cap = 128, len = 0;
    char* buf = (char*)malloc(cap);
    if (!buf) return out;
    int c;
    while ((c = fgetc(stdin)) != EOF) {
        if (len + 1 >= cap) {
            size_t new_cap = cap * 2;
            char* nb = (char*)realloc(buf, new_cap);
            if (!nb) { free(buf); return out; }
            buf = nb; cap = new_cap;
        }
        buf[len++] = (char)c;
        if (c == '\n') break;
    }
    if (len == 0 && c == EOF) { free(buf); return out; }
    buf[len] = '\0';
    out.data = buf;
    out.len = (int64_t)len;
    return out;
}

StringVal rt_stdin_read_all(void) {
    StringVal out = {NULL, 0};
    size_t cap = 4096, len = 0;
    char* buf = (char*)malloc(cap);
    if (!buf) return out;
    int c;
    while ((c = fgetc(stdin)) != EOF) {
        if (len + 1 >= cap) {
            size_t new_cap = cap * 2;
            char* nb = (char*)realloc(buf, new_cap);
            if (!nb) { free(buf); return out; }
            buf = nb; cap = new_cap;
        }
        buf[len++] = (char)c;
    }
    if (len == 0) { free(buf); return out; }
    buf[len] = '\0';
    out.data = buf;
    out.len = (int64_t)len;
    return out;
}

void* rt_args_get_handle(int32_t index) {
    return rt_string_handle_from_value(rt_args_get(index));
}

void* rt_env_get_handle(void* name) {
    return rt_string_handle_from_value(rt_env_get(rt_string_obj_value(name)));
}

int32_t rt_env_set_string(void* name, void* value) {
    return rt_env_set(rt_string_obj_value(name), rt_string_obj_value(value));
}

int32_t rt_process_system_string(void* cmd) {
    return rt_process_system(rt_string_obj_value(cmd));
}

__attribute__((noreturn))
void rt_panic_msg_string(void* msg) {
    rt_panic_msg(rt_string_obj_value(msg));
    /* Unreachable — rt_panic_msg is noreturn but the compiler can't always
     * tell through the indirect call wrapper. Keep ``abort`` as belt and
     * braces. */
    abort();
}

void* rt_stdin_read_line_handle(void) {
    return rt_string_handle_from_value(rt_stdin_read_line());
}

void* rt_stdin_read_all_handle(void) {
    return rt_string_handle_from_value(rt_stdin_read_all());
}

/* ============================================================================
 * Path manipulation
 * Pure string operations (POSIX-style separators). They don't touch the FS
 * except for `rt_path_*_exists` which calls stat(2).
 * ============================================================================ */

static char* rt_str_dup_n(const char* data, int64_t len) {
    if (len < 0) len = 0;
    char* buf = (char*)malloc((size_t)len + 1);
    if (!buf) return NULL;
    if (len > 0 && data) memcpy(buf, data, (size_t)len);
    buf[len] = '\0';
    return buf;
}

static StringVal rt_str_take(char* data, int64_t len) {
    StringVal s = {data, len};
    return s;
}

StringVal rt_path_join(StringVal a, StringVal b) {
    if (b.len > 0 && b.data && b.data[0] == '/') {
        /* Absolute right-hand side wins. */
        char* buf = rt_str_dup_n(b.data, b.len);
        return rt_str_take(buf, b.len);
    }
    if (a.len == 0) {
        char* buf = rt_str_dup_n(b.data, b.len);
        return rt_str_take(buf, b.len);
    }
    if (b.len == 0) {
        char* buf = rt_str_dup_n(a.data, a.len);
        return rt_str_take(buf, a.len);
    }
    int64_t need_sep = (a.data[a.len - 1] != '/') ? 1 : 0;
    int64_t total = a.len + need_sep + b.len;
    char* buf = (char*)malloc((size_t)total + 1);
    if (!buf) { StringVal e = {NULL, 0}; return e; }
    memcpy(buf, a.data, (size_t)a.len);
    if (need_sep) buf[a.len] = '/';
    memcpy(buf + a.len + need_sep, b.data, (size_t)b.len);
    buf[total] = '\0';
    return rt_str_take(buf, total);
}

StringVal rt_path_dirname(StringVal p) {
    if (p.len == 0 || !p.data) {
        char* buf = rt_str_dup_n(".", 1);
        return rt_str_take(buf, 1);
    }
    /* Strip trailing slashes (but keep the first one for root). */
    int64_t end = p.len;
    while (end > 1 && p.data[end - 1] == '/') end--;
    int64_t slash = -1;
    for (int64_t i = end - 1; i >= 0; i--) {
        if (p.data[i] == '/') { slash = i; break; }
    }
    if (slash < 0) {
        char* buf = rt_str_dup_n(".", 1);
        return rt_str_take(buf, 1);
    }
    if (slash == 0) {
        char* buf = rt_str_dup_n("/", 1);
        return rt_str_take(buf, 1);
    }
    char* buf = rt_str_dup_n(p.data, slash);
    return rt_str_take(buf, slash);
}

StringVal rt_path_basename(StringVal p) {
    if (p.len == 0 || !p.data) {
        StringVal e = {NULL, 0};
        return e;
    }
    int64_t end = p.len;
    while (end > 1 && p.data[end - 1] == '/') end--;
    int64_t slash = -1;
    for (int64_t i = end - 1; i >= 0; i--) {
        if (p.data[i] == '/') { slash = i; break; }
    }
    int64_t start = (slash < 0) ? 0 : slash + 1;
    int64_t len = end - start;
    char* buf = rt_str_dup_n(p.data + start, len);
    return rt_str_take(buf, len);
}

StringVal rt_path_extension(StringVal p) {
    StringVal out = {NULL, 0};
    if (p.len == 0 || !p.data) return out;
    int64_t dot = -1;
    int64_t slash = -1;
    for (int64_t i = p.len - 1; i >= 0; i--) {
        if (p.data[i] == '/') { slash = i; break; }
        if (p.data[i] == '.' && dot < 0) dot = i;
    }
    if (dot < 0 || dot <= slash + 1) return out;  /* leading dot files have no ext */
    int64_t len = p.len - (dot + 1);
    char* buf = rt_str_dup_n(p.data + dot + 1, len);
    return rt_str_take(buf, len);
}

int32_t rt_path_exists(StringVal p) {
    if (p.len == 0 || !p.data) return 0;
    char stackbuf[1024];
    char* c = stackbuf;
    if ((size_t)p.len + 1 > sizeof(stackbuf)) {
        c = (char*)malloc((size_t)p.len + 1);
        if (!c) return 0;
    }
    memcpy(c, p.data, (size_t)p.len);
    c[p.len] = '\0';
    struct stat st;
    int rc = stat(c, &st);
    if (c != stackbuf) free(c);
    return (rc == 0) ? 1 : 0;
}

int32_t rt_path_is_dir(StringVal p) {
    if (p.len == 0 || !p.data) return 0;
    char stackbuf[1024];
    char* c = stackbuf;
    if ((size_t)p.len + 1 > sizeof(stackbuf)) {
        c = (char*)malloc((size_t)p.len + 1);
        if (!c) return 0;
    }
    memcpy(c, p.data, (size_t)p.len);
    c[p.len] = '\0';
    struct stat st;
    int rc = stat(c, &st);
    int is_dir = (rc == 0 && S_ISDIR(st.st_mode)) ? 1 : 0;
    if (c != stackbuf) free(c);
    return is_dir;
}

int32_t rt_path_is_file(StringVal p) {
    if (p.len == 0 || !p.data) return 0;
    char stackbuf[1024];
    char* c = stackbuf;
    if ((size_t)p.len + 1 > sizeof(stackbuf)) {
        c = (char*)malloc((size_t)p.len + 1);
        if (!c) return 0;
    }
    memcpy(c, p.data, (size_t)p.len);
    c[p.len] = '\0';
    struct stat st;
    int rc = stat(c, &st);
    int is_file = (rc == 0 && S_ISREG(st.st_mode)) ? 1 : 0;
    if (c != stackbuf) free(c);
    return is_file;
}

StringVal rt_path_resolve(StringVal p) {
    StringVal out = {NULL, 0};
    if (p.len == 0 || !p.data) return out;
#if defined(__linux__) || defined(__APPLE__)
    char stackbuf[1024];
    char* c = stackbuf;
    if ((size_t)p.len + 1 > sizeof(stackbuf)) {
        c = (char*)malloc((size_t)p.len + 1);
        if (!c) return out;
    }
    memcpy(c, p.data, (size_t)p.len);
    c[p.len] = '\0';
    char resolved[4096];
    char* rc = realpath(c, resolved);
    if (c != stackbuf) free(c);
    if (!rc) {
        /* Fall back to the input string when the path doesn't exist. */
        char* buf = rt_str_dup_n(p.data, p.len);
        return rt_str_take(buf, p.len);
    }
    int64_t len = (int64_t)strlen(resolved);
    char* buf = rt_str_dup_n(resolved, len);
    return rt_str_take(buf, len);
#else
    char* buf = rt_str_dup_n(p.data, p.len);
    return rt_str_take(buf, p.len);
#endif
}

/* Directory listing: returns a heap array packed as a gvec of StringVal.
 * Each entry is a fresh malloc'd copy. Caller must free both the inner
 * strings and the gvec itself via rt_gvec_free. */
void* rt_dir_list(StringVal path) {
#if defined(__linux__) || defined(__APPLE__)
    if (path.len == 0 || !path.data) return NULL;
    char stackbuf[1024];
    char* c = stackbuf;
    if ((size_t)path.len + 1 > sizeof(stackbuf)) {
        c = (char*)malloc((size_t)path.len + 1);
        if (!c) return NULL;
    }
    memcpy(c, path.data, (size_t)path.len);
    c[path.len] = '\0';
    DIR* d = opendir(c);
    if (c != stackbuf) free(c);
    if (!d) return NULL;

    void* vec = rt_gvec_new(16, (int32_t)sizeof(StringVal), 0);
    if (!vec) { closedir(d); return NULL; }
    struct dirent* ent;
    while ((ent = readdir(d)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        int64_t nlen = (int64_t)strlen(ent->d_name);
        char* buf = rt_str_dup_n(ent->d_name, nlen);
        if (!buf) continue;
        StringVal entry = {buf, nlen};
        vec = rt_gvec_push(vec, &entry);
    }
    closedir(d);
    return vec;
#else
    (void)path;
    return NULL;
#endif
}

void* rt_path_join_handle(void* a, void* b) {
    return rt_string_handle_from_value(rt_path_join(rt_string_obj_value(a), rt_string_obj_value(b)));
}

void* rt_path_dirname_handle(void* p) {
    return rt_string_handle_from_value(rt_path_dirname(rt_string_obj_value(p)));
}

void* rt_path_basename_handle(void* p) {
    return rt_string_handle_from_value(rt_path_basename(rt_string_obj_value(p)));
}

void* rt_path_extension_handle(void* p) {
    return rt_string_handle_from_value(rt_path_extension(rt_string_obj_value(p)));
}

int32_t rt_path_exists_string(void* p) { return rt_path_exists(rt_string_obj_value(p)); }
int32_t rt_path_is_dir_string(void* p) { return rt_path_is_dir(rt_string_obj_value(p)); }
int32_t rt_path_is_file_string(void* p) { return rt_path_is_file(rt_string_obj_value(p)); }

void* rt_path_resolve_handle(void* p) {
    return rt_string_handle_from_value(rt_path_resolve(rt_string_obj_value(p)));
}

void* rt_dir_list_handles(void* path_obj) {
    void* old_vec = rt_dir_list(rt_string_obj_value(path_obj));
    if (old_vec == NULL) return NULL;
    GVecHeader* old_h = (GVecHeader*)old_vec;
    void* new_vec = rt_gvec_new(old_h->len > 0 ? old_h->len : 1, (int32_t)sizeof(void*), 0);
    if (new_vec == NULL) {
        rt_gvec_free(old_vec);
        return NULL;
    }
    unsigned char* old_data = (unsigned char*)old_vec + sizeof(GVecHeader);
    for (int32_t i = 0; i < old_h->len; i++) {
        StringVal entry = *(StringVal*)(old_data + (size_t)i * sizeof(StringVal));
        void* handle = rt_string_handle_from_value(entry);
        new_vec = rt_gvec_push(new_vec, &handle);
    }
    free(old_vec);
    return new_vec;
}

/* ============================================================================
 * C entry point — wraps the user's renamed `__rolang_user_main`.
 *
 * The codegen `_rename_user_main` pass renames the user `main()` to this
 * internal name so we can own the real entry point and capture argv. If
 * you build the runtime standalone (no user code), the linker will report
 * `__rolang_user_main` as undefined — that is the expected failure mode.
 * ============================================================================ */

extern int32_t __rolang_user_main(void);

int main(int argc, char** argv) {
    rt_argc_global = argc;
    rt_argv_global = argv;
    int32_t rc = __rolang_user_main();
    return (int)rc;
}
