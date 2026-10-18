/* buckthorn/c_src/bt_logger.c */
#include "bt_logger.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

#define BT_FLUSH_BATCH_SIZE 64
#define BT_LINE_BUF_SIZE    512

static inline uint64_t bt_now_ns(void) {
    struct timespec ts;
#ifdef CLOCK_REALTIME_COARSE
    clock_gettime(CLOCK_REALTIME_COARSE, &ts);
#else
    clock_gettime(CLOCK_REALTIME, &ts);
#endif
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static size_t next_power_of_two(size_t n) {
    size_t p = 1024;
    while (p < n) p <<= 1;
    return p;
}

static inline void bt_copy_json_safe(char *dst, const char *src, size_t len) {
    for (size_t i = 0; i < len; i++) {
        char c = src[i];
        if (c == '"' || c == '\\' || c == '\r' || c == '\n' || (unsigned char)c < 0x20) {
            dst[i] = '_';
        } else {
            dst[i] = c;
        }
    }
    dst[len] = '\0';
}

static const char *level_to_str(uint8_t level) {
    switch (level) {
        case BT_LOG_LEVEL_DEBUG: return "DEBUG";
        case BT_LOG_LEVEL_INFO:  return "INFO";
        case BT_LOG_LEVEL_WARN:  return "WARN";
        case BT_LOG_LEVEL_ERROR: return "ERROR";
        default:                 return "INFO";
    }
}

/* Formats a single bt_log_entry_t into a JSON line inside line_buf */
static size_t format_entry_json(const bt_log_entry_t *e, char *line_buf, size_t cap) {
    char clean_msg[sizeof(e->msg)];
    size_t mlen = e->msg_len < (sizeof(clean_msg) - 1) ? e->msg_len : (sizeof(clean_msg) - 1);
    bt_copy_json_safe(clean_msg, e->msg, mlen);

    int pos = snprintf(
        line_buf, cap,
        "{\"ts_ns\":%llu,\"level\":\"%s\",\"msg\":\"%.*s\"",
        (unsigned long long)e->ts_ns,
        level_to_str(e->level),
        (int)mlen, clean_msg
    );
    if (pos < 0 || (size_t)pos >= cap - 4) return 0;

    for (uint8_t i = 0; i < e->num_kvs && i < BT_LOG_MAX_KVS; i++) {
        const bt_log_kv_t *kv = &e->kvs[i];
        int added = 0;
        if (kv->val_type == BT_LOG_VAL_INT) {
            added = snprintf(
                line_buf + pos, cap - (size_t)pos,
                ",\"%.*s\":%lld",
                (int)kv->key_len, kv->key, (long long)kv->v.i_val
            );
        } else if (kv->val_type == BT_LOG_VAL_FLOAT) {
            added = snprintf(
                line_buf + pos, cap - (size_t)pos,
                ",\"%.*s\":%.6g",
                (int)kv->key_len, kv->key, kv->v.f_val
            );
        } else if (kv->val_type == BT_LOG_VAL_BOOL) {
            added = snprintf(
                line_buf + pos, cap - (size_t)pos,
                ",\"%.*s\":%s",
                (int)kv->key_len, kv->key, kv->v.b_val ? "true" : "false"
            );
        } else if (kv->val_type == BT_LOG_VAL_STR) {
            char clean_val[64];
            size_t slen = kv->str_len < (sizeof(clean_val) - 1) ? kv->str_len : (sizeof(clean_val) - 1);
            bt_copy_json_safe(clean_val, kv->v.s_val, slen);
            added = snprintf(
                line_buf + pos, cap - (size_t)pos,
                ",\"%.*s\":\"%.*s\"",
                (int)kv->key_len, kv->key, (int)slen, clean_val
            );
        }
        if (added > 0 && (size_t)(pos + added) < cap - 3) {
            pos += added;
        }
    }

    line_buf[pos++] = '}';
    line_buf[pos++] = '\n';
    return (size_t)pos;
}

/* Drains up to 64 entries per batch and flushes with a single writev() syscall */
static void drain_ring_buffer(bt_logger_t *logger) {
    char batch_lines[BT_FLUSH_BATCH_SIZE][BT_LINE_BUF_SIZE];
    struct iovec iov[BT_FLUSH_BATCH_SIZE];

    while (1) {
        size_t r = atomic_load_explicit(&logger->read_idx, memory_order_relaxed);
        size_t w = atomic_load_explicit(&logger->write_idx, memory_order_acquire);
        if (r == w) break;

        int batch_count = 0;
        while (r < w && batch_count < BT_FLUSH_BATCH_SIZE) {
            const bt_log_entry_t *entry = &logger->slots[r & logger->mask];
            size_t len = format_entry_json(entry, batch_lines[batch_count], BT_LINE_BUF_SIZE);
            iov[batch_count].iov_base = batch_lines[batch_count];
            iov[batch_count].iov_len = len;
            batch_count++;
            r++;
        }

        if (batch_count > 0 && logger->fd >= 0) {
            ssize_t _unused = writev(logger->fd, iov, batch_count);
            (void)_unused;
        }

        atomic_store_explicit(&logger->read_idx, r, memory_order_release);
    }
}

static void *logger_consumer_thread(void *arg) {
    bt_logger_t *logger = (bt_logger_t *)arg;
    struct timespec sleep_ts = {0, 500000}; /* 500 microseconds poll interval */

    while (atomic_load_explicit(&logger->running, memory_order_acquire)) {
        drain_ring_buffer(logger);
        nanosleep(&sleep_ts, NULL);
    }
    /* Final drain on shutdown so zero queued logs are lost */
    drain_ring_buffer(logger);
    return NULL;
}

bt_logger_t *bt_logger_create(const char *filepath, size_t capacity) {
    bt_logger_t *logger = (bt_logger_t *)calloc(1, sizeof(bt_logger_t));
    if (!logger) return NULL;

    size_t cap = next_power_of_two(capacity);
    logger->slots = (bt_log_entry_t *)calloc(cap, sizeof(bt_log_entry_t));
    if (!logger->slots) {
        free(logger);
        return NULL;
    }

    logger->capacity = cap;
    logger->mask = cap - 1;
    atomic_init(&logger->write_idx, 0);
    atomic_init(&logger->read_idx, 0);
    atomic_init(&logger->running, 1);

    if (filepath && strlen(filepath) > 0) {
        logger->fd = open(filepath, O_CREAT | O_WRONLY | O_APPEND, 0644);
        logger->owns_fd = 1;
    } else {
        logger->fd = STDOUT_FILENO;
        logger->owns_fd = 0;
    }

    if (pthread_create(&logger->thread, NULL, logger_consumer_thread, logger) != 0) {
        if (logger->owns_fd && logger->fd >= 0) close(logger->fd);
        free(logger->slots);
        free(logger);
        return NULL;
    }

    return logger;
}

bt_log_entry_t *bt_logger_acquire_slot(bt_logger_t *logger, uint8_t level,
                                       const char *msg, size_t msg_len) {
    if (!logger || !atomic_load_explicit(&logger->running, memory_order_relaxed)) {
        return NULL;
    }

    size_t w = atomic_load_explicit(&logger->write_idx, memory_order_relaxed);
    if (w - logger->cached_read_idx >= logger->capacity) {
        logger->cached_read_idx = atomic_load_explicit(&logger->read_idx, memory_order_acquire);
        if (w - logger->cached_read_idx >= logger->capacity) {
            /* Ring buffer full: drop log rather than blocking the event loop! */
            logger->dropped_count++;
            return NULL;
        }
    }

    bt_log_entry_t *slot = &logger->slots[w & logger->mask];
    slot->ts_ns = bt_now_ns();
    slot->level = level;
    slot->num_kvs = 0;

    size_t copy_len = msg_len < (sizeof(slot->msg) - 1) ? msg_len : (sizeof(slot->msg) - 1);
    memcpy(slot->msg, msg, copy_len);
    slot->msg[copy_len] = '\0';
    slot->msg_len = (uint8_t)copy_len;

    return slot;
}

void bt_logger_publish_slot(bt_logger_t *logger) {
    size_t w = atomic_load_explicit(&logger->write_idx, memory_order_relaxed);
    atomic_store_explicit(&logger->write_idx, w + 1, memory_order_release);
}

void bt_logger_log_http_access(
    bt_logger_t *logger,
    const char *method, size_t method_len,
    const char *path, size_t path_len,
    int status_code,
    int64_t latency_ns
) {
    bt_log_entry_t *slot = bt_logger_acquire_slot(logger, BT_LOG_LEVEL_INFO, "http_access", 11);
    if (!slot) return;

    /* KV 0: method */
    bt_log_kv_t *kv0 = &slot->kvs[0];
    memcpy(kv0->key, "method", 6);
    kv0->key_len = 6;
    kv0->val_type = BT_LOG_VAL_STR;
    size_t mlen = method_len < 47 ? method_len : 47;
    memcpy(kv0->v.s_val, method, mlen);
    kv0->str_len = (uint8_t)mlen;

    /* KV 1: path */
    bt_log_kv_t *kv1 = &slot->kvs[1];
    memcpy(kv1->key, "path", 4);
    kv1->key_len = 4;
    kv1->val_type = BT_LOG_VAL_STR;
    size_t plen = path_len < 47 ? path_len : 47;
    memcpy(kv1->v.s_val, path, plen);
    kv1->str_len = (uint8_t)plen;

    /* KV 2: status */
    bt_log_kv_t *kv2 = &slot->kvs[2];
    memcpy(kv2->key, "status", 6);
    kv2->key_len = 6;
    kv2->val_type = BT_LOG_VAL_INT;
    kv2->v.i_val = (int64_t)status_code;

    /* KV 3: latency_ns */
    bt_log_kv_t *kv3 = &slot->kvs[3];
    memcpy(kv3->key, "latency_ns", 10);
    kv3->key_len = 10;
    kv3->val_type = BT_LOG_VAL_INT;
    kv3->v.i_val = latency_ns;

    slot->num_kvs = 4;
    bt_logger_publish_slot(logger);
}

void bt_logger_flush_and_stop(bt_logger_t *logger) {
    if (!logger) return;
    int expected = 1;
    if (atomic_compare_exchange_strong(&logger->running, &expected, 0)) {
        pthread_join(logger->thread, NULL);
        if (logger->owns_fd && logger->fd >= 0) {
            fsync(logger->fd);
            close(logger->fd);
            logger->fd = -1;
        }
    }
}

void bt_logger_destroy(bt_logger_t *logger) {
    if (!logger) return;
    bt_logger_flush_and_stop(logger);
    if (logger->slots) {
        free(logger->slots);
        logger->slots = NULL;
    }
    free(logger);
}
