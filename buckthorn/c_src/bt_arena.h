/* buckthorn/c_src/bt_arena.h */
#ifndef BT_ARENA_H
#define BT_ARENA_H

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

#if defined(__GNUC__) || defined(__clang__)
#define BT_ARENA_LIKELY(x)   __builtin_expect(!!(x), 1)
#define BT_ARENA_UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
#define BT_ARENA_LIKELY(x)   (x)
#define BT_ARENA_UNLIKELY(x) (x)
#endif

#define BT_ARENA_DEFAULT_SIZE (64 * 1024)
#define BT_ARENA_MAX_ALLOC    (16 * 1024 * 1024) /* 16MB hard ceiling per allocation */

typedef struct bt_arena_overflow_s {
    struct bt_arena_overflow_s *next;
    uint8_t data[];
} bt_arena_overflow_t;

typedef struct {
    uint8_t *buf;
    size_t capacity;
    size_t offset;
    bt_arena_overflow_t *overflow_head;
} bt_arena_t;

static inline int bt_arena_init(bt_arena_t *arena, size_t capacity) {
    if (!arena || capacity == 0) return -1;
    arena->buf = (uint8_t *)malloc(capacity);
    if (!arena->buf) return -1;
    arena->capacity = capacity;
    arena->offset = 0;
    arena->overflow_head = NULL;
    return 0;
}

static inline void *bt_arena_alloc(bt_arena_t *arena, size_t size) {
    if (BT_ARENA_UNLIKELY(!arena || size == 0 || size > BT_ARENA_MAX_ALLOC)) {
        return NULL;
    }

    /* 8-byte alignment for 64-bit CPU cache efficiency */
    size_t aligned = (size + 7) & ~((size_t)7);

    /* Tier-1 Fast Path: O(1) pointer bump inside the pre-allocated 64KB slab */
    if (BT_ARENA_LIKELY(aligned <= arena->capacity - arena->offset)) {
        void *ptr = arena->buf + arena->offset;
        arena->offset += aligned;
        return ptr;
    }

    /* Tier-2 Overflow Path: On-demand slab for large responses/payloads, freed on reset */
    bt_arena_overflow_t *blk = (bt_arena_overflow_t *)malloc(
        sizeof(bt_arena_overflow_t) + aligned
    );
    if (BT_ARENA_UNLIKELY(!blk)) {
        return NULL;
    }
    blk->next = arena->overflow_head;
    arena->overflow_head = blk;
    return (void *)blk->data;
}

static inline void bt_arena_reset(bt_arena_t *arena) {
    if (!arena) return;
    arena->offset = 0;
    if (BT_ARENA_UNLIKELY(arena->overflow_head != NULL)) {
        bt_arena_overflow_t *cur = arena->overflow_head;
        while (cur) {
            bt_arena_overflow_t *next = cur->next;
            free(cur);
            cur = next;
        }
        arena->overflow_head = NULL;
    }
}

static inline void bt_arena_destroy(bt_arena_t *arena) {
    if (!arena) return;
    bt_arena_reset(arena);
    if (arena->buf) {
        free(arena->buf);
        arena->buf = NULL;
    }
    arena->capacity = 0;
}

#endif /* BT_ARENA_H */
