/* buckthorn/c_src/bt_arena.h */
#ifndef BT_ARENA_H
#define BT_ARENA_H

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define BT_ARENA_DEFAULT_SIZE (64 * 1024) /* 64 KB per connection */

typedef struct {
    uint8_t *buf;
    size_t capacity;
    size_t offset;
} bt_arena_t;

static inline int bt_arena_init(bt_arena_t *arena, size_t capacity) {
    arena->buf = (uint8_t *)malloc(capacity);
    if (!arena->buf) return -1;
    arena->capacity = capacity;
    arena->offset = 0;
    return 0;
}

static inline void *bt_arena_alloc(bt_arena_t *arena, size_t size) {
    /* Align to 8 bytes */
    size_t aligned = (size + 7) & ~((size_t)7);
    if (arena->offset + aligned > arena->capacity) {
        return NULL; /* Out of arena memory */
    }
    void *ptr = arena->buf + arena->offset;
    arena->offset += aligned;
    return ptr;
}

static inline void bt_arena_reset(bt_arena_t *arena) {
    arena->offset = 0;
}

static inline void bt_arena_destroy(bt_arena_t *arena) {
    if (arena->buf) {
        free(arena->buf);
        arena->buf = NULL;
    }
    arena->capacity = 0;
    arena->offset = 0;
}

#endif /* BT_ARENA_H */
